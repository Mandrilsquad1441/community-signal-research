from __future__ import annotations

import argparse
import errno
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


OPERATOR_PATH = Path(__file__).resolve().parents[1] / "run_trials.py"
SPEC = importlib.util.spec_from_file_location("community_signal_eval_operator", OPERATOR_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Cannot import {OPERATOR_PATH}")
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)
VALID_TRIAL_ID = "trial-0123456789abcdef"


def write_core(trial: Path) -> None:
    for relative in operator.CORE_FILES:
        path = trial / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative.as_posix()}\n", encoding="utf-8")


def trial_record(trial: Path, trial_id: str = VALID_TRIAL_ID) -> dict[str, object]:
    _, hashes = operator.build_prompt(trial)
    return {
        "trial_id": trial_id,
        "pair_id": "pair-test",
        "case_id": "case-01-test",
        "replicate": 1,
        "model_seed": 123,
        "condition": "baseline",
        "trial_file_hashes": hashes,
    }


class OperatorTests(unittest.TestCase):
    def create_junction(self, link: Path, target: Path) -> None:
        if os.name != "nt":
            self.skipTest("Windows directory-junction regression")
        completed = subprocess.run(
            [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest(f"junction creation is unavailable: {completed.stderr or completed.stdout}")
        self.assertTrue(operator.is_link_or_reparse(link))

    def test_request_seed_option_detection_is_exact_and_deduplicated(self) -> None:
        help_text = "Options:\n  --seed <INTEGER>\n  --request-seed=VALUE\n  --seed <INTEGER>\n  --seedling\n"
        self.assertEqual(
            {"--seed", "--request-seed"},
            set(operator.extract_request_seed_options(help_text)),
        )
        self.assertEqual([], operator.extract_request_seed_options("Options: --seedling --model-seed"))

    def test_request_seed_capability_record_fails_closed_when_missing_or_malformed(self) -> None:
        for identity in ({}, {"request_seed_options": None}, {"request_seed_options": "--seed"}):
            with self.subTest(identity=identity), self.assertRaisesRegex(
                ValueError, "malformed request-seed capability data"
            ):
                operator.require_request_seed_unsupported(identity)

    def test_executable_identity_binds_help_and_version_to_one_binary_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex.exe"
            executable.write_bytes(b"stable-codex-binary")
            version = subprocess.CompletedProcess(
                [str(executable), "--version"], 0, stdout="codex-cli test\n", stderr=""
            )
            help_result = subprocess.CompletedProcess(
                [str(executable), "exec", "--help"],
                0,
                stdout="Usage: codex exec\n  --model MODEL\n",
                stderr="warning text\n",
            )
            isolation_result = subprocess.CompletedProcess(
                [str(executable), "debug", "prompt-input"],
                0,
                stdout=json.dumps(
                    [
                        {
                            "type": "message",
                            "role": "user",
                            "content": operator.PROMPT_ISOLATION_MARKER,
                        }
                    ]
                ),
                stderr="metadata-only warning\n",
            )
            with (
                mock.patch.object(operator.shutil, "which", return_value=str(executable)),
                mock.patch.object(
                    operator,
                    "run_managed_capture",
                    side_effect=[version, help_result, isolation_result],
                ),
            ):
                identity = operator.executable_identity("codex")
            self.assertEqual(operator.sha256_file(executable), identity["binary_sha256"])
            self.assertEqual(
                operator.sha256_bytes(help_result.stdout.encode() + b"\x00" + help_result.stderr.encode()),
                identity["exec_help_sha256"],
            )
            self.assertEqual([], identity["request_seed_options"])
            self.assertFalse(identity["prompt_isolation"]["skills_include_instructions"])
            self.assertFalse(identity["prompt_isolation"]["bundled_skills_enabled"])

    def test_prompt_isolation_probe_fails_closed_on_model_visible_skill_catalog(self) -> None:
        visible_catalog = subprocess.CompletedProcess(
            ["codex", "debug", "prompt-input"],
            0,
            stdout=json.dumps(
                [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "## Available skills\n- unsafe (file: C:/x/SKILL.md)",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": operator.PROMPT_ISOLATION_MARKER,
                    },
                ]
            ),
            stderr="",
        )
        with (
            mock.patch.object(operator, "run_managed_capture", return_value=visible_catalog),
            self.assertRaisesRegex(ValueError, "still exposes host skill instructions"),
        ):
            operator.prompt_isolation_probe("codex")

    def test_executable_identity_rejects_binary_swap_during_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex.exe"
            executable.write_bytes(b"binary-a")

            def swap_after_version(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                executable.write_bytes(b"binary-b")
                return subprocess.CompletedProcess(argv, 0, stdout="codex-cli test\n", stderr="")

            with (
                mock.patch.object(operator.shutil, "which", return_value=str(executable)),
                mock.patch.object(operator, "run_managed_capture", side_effect=swap_after_version),
                self.assertRaisesRegex(ValueError, "hash changed"),
            ):
                operator.executable_identity("codex")

    def test_preflight_rejects_new_request_seed_capability_before_catalog_lookup(self) -> None:
        args = argparse.Namespace(codex="codex", model="gpt-5.4-mini", reasoning="low")
        identity = {
            "resolved_path": "codex",
            "request_seed_options": ["--request-seed"],
        }
        with (
            mock.patch.object(operator, "executable_identity", return_value=identity),
            mock.patch.object(operator, "model_catalog_entry") as catalog,
            self.assertRaisesRegex(ValueError, "update the operator to apply every allocated model seed"),
        ):
            operator.preflight(args)
        catalog.assert_not_called()

    def test_batch_rejects_new_request_seed_capability_before_writing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            operator.write_json(
                run_dir / "allocation.private.json",
                {
                    "seed": 20260830,
                    "replicates": 5,
                    "fixture_hashes": {},
                    "skill_resource_hashes": {},
                    "trials": [],
                    "dispatch_order": [],
                },
            )
            args = argparse.Namespace(
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=1,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            identity = {
                "resolved_path": "codex",
                "request_seed_options": ["--seed"],
            }
            with (
                mock.patch.object(operator, "executable_identity", return_value=identity),
                mock.patch.object(operator, "model_catalog_entry") as catalog,
                self.assertRaisesRegex(ValueError, "Codex exec now exposes request-seed option"),
            ):
                operator.run_batch(args)
            catalog.assert_not_called()
            self.assertFalse((run_dir / "operator-config.json").exists())

    def test_baseline_prompt_contains_only_core_staged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trial = Path(temporary) / "trial"
            trial.mkdir()
            write_core(trial)
            prompt, hashes = operator.build_prompt(trial)
            self.assertEqual({path.as_posix() for path in operator.CORE_FILES}, set(hashes))
            self.assertNotIn("community-signal-research/SKILL.md", prompt)
            for relative in operator.CORE_FILES:
                self.assertIn(relative.as_posix(), prompt)

    def test_skill_prompt_requires_and_includes_complete_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trial = Path(temporary) / "trial"
            trial.mkdir()
            write_core(trial)
            partial = trial / operator.TREATMENT_FILES[0]
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text("skill\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "partial skill treatment"):
                operator.build_prompt(trial)
            for relative in operator.TREATMENT_FILES:
                path = trial / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"treatment for {relative.as_posix()}\n", encoding="utf-8")
            prompt, hashes = operator.build_prompt(trial)
            self.assertEqual(len(operator.CORE_FILES) + len(operator.TREATMENT_FILES), len(hashes))
            self.assertIn("skill/community-signal-research/SKILL.md", prompt)

    def test_codex_arguments_disable_tools_and_use_ephemeral_read_only_mode(self) -> None:
        trial = Path("C:/synthetic/trial")
        response = Path("C:/external-run/dispatch/trial-test/response.raw.txt")
        argv = operator.codex_argv(
            "codex.exe", trial, response, "gpt-5.4-mini", "low"
        )
        self.assertIn("--ephemeral", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("read-only", argv)
        self.assertNotIn("--search", argv)
        disabled = {argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--disable"}
        self.assertEqual(set(operator.DISABLED_FEATURES), disabled)
        self.assertIn('model_reasoning_effort="low"', argv)
        self.assertIn("suppress_unstable_features_warning=true", argv)
        self.assertIn("skills.include_instructions=false", argv)
        self.assertIn("skills.bundled.enabled=false", argv)
        self.assertEqual(str(trial), argv[argv.index("--cd") + 1])
        self.assertEqual(
            str(trial / "response.schema.json"),
            argv[argv.index("--output-schema") + 1],
        )
        self.assertEqual(str(response), argv[argv.index("--output-last-message") + 1])
        self.assertNotIn("response.json", argv)

    def test_frozen_response_schema_uses_the_strict_structured_output_subset(self) -> None:
        allowed_keywords = {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "items",
            "enum",
        }

        def validate_node(node: object, path: str) -> None:
            self.assertIsInstance(node, dict, path)
            self.assertLessEqual(set(node), allowed_keywords, path)
            self.assertIn("type", node, path)
            node_type = node["type"]
            if node_type == "object":
                properties = node.get("properties")
                self.assertIsInstance(properties, dict, path)
                self.assertEqual(set(properties), set(node.get("required", [])), path)
                self.assertIs(node.get("additionalProperties"), False, path)
                for name, child in properties.items():
                    validate_node(child, f"{path}.{name}")
            elif node_type == "array":
                validate_node(node.get("items"), f"{path}[]")
            elif isinstance(node_type, list):
                self.assertEqual({"null", "string"}, set(node_type), path)
            else:
                self.assertIn(node_type, {"integer", "string"}, path)

        validate_node(operator.load_json(operator.RESPONSE_SCHEMA_PATH), "response")

    def test_smoke_uses_the_exact_frozen_schema_and_requires_the_exact_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "smoke"

            def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                response_path = Path(argv[argv.index("--output-last-message") + 1])
                response_path.write_text(
                    json.dumps(operator.SMOKE_EXPECTED_RESPONSE, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            args = argparse.Namespace(
                out_dir=out_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                timeout_seconds=30,
            )
            identity = {
                "requested_command": "codex",
                "resolved_path": "codex",
                "binary_sha256": "a" * 64,
                "version_output": "test",
            }
            catalog = {"supported_reasoning_levels": ["low"]}
            with (
                mock.patch.object(operator, "executable_identity", return_value=identity),
                mock.patch.object(operator, "model_catalog_entry", return_value=(catalog, "b" * 64)),
                mock.patch.object(operator, "require_executable_hash"),
                mock.patch.object(operator, "run_managed_capture", side_effect=fake_run),
            ):
                record = operator.smoke_test(args)

            self.assertEqual(
                operator.RESPONSE_SCHEMA_PATH.read_bytes(),
                (out_dir / "response.schema.json").read_bytes(),
            )
            self.assertTrue(record["response_matches_expected"])
            self.assertIsNone(record["response_validation_error"])

    def test_smoke_preserves_and_rejects_a_nonmatching_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "smoke"

            def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                response_path = Path(argv[argv.index("--output-last-message") + 1])
                response_path.write_text('{"schema_version":"1.0"}\n', encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            args = argparse.Namespace(
                out_dir=out_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                timeout_seconds=30,
            )
            identity = {
                "requested_command": "codex",
                "resolved_path": "codex",
                "binary_sha256": "a" * 64,
                "version_output": "test",
            }
            catalog = {"supported_reasoning_levels": ["low"]}
            with (
                mock.patch.object(operator, "executable_identity", return_value=identity),
                mock.patch.object(operator, "model_catalog_entry", return_value=(catalog, "b" * 64)),
                mock.patch.object(operator, "require_executable_hash"),
                mock.patch.object(operator, "run_managed_capture", side_effect=fake_run),
                self.assertRaisesRegex(ValueError, "Codex smoke failed"),
            ):
                operator.smoke_test(args)

            record = json.loads((out_dir / "smoke.json").read_text(encoding="utf-8"))
            self.assertFalse(record["response_matches_expected"])
            self.assertTrue((out_dir / "response.raw.txt").is_file())

    def test_smoke_rejects_boolean_and_float_substitutes_for_integer_zero(self) -> None:
        for label, replacement in (("boolean", False), ("float", 0.0)):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                out_dir = Path(temporary) / "smoke"
                response = json.loads(json.dumps(operator.SMOKE_EXPECTED_RESPONSE))
                response["independent_support"]["authors"] = replacement

                def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                    response_path = Path(argv[argv.index("--output-last-message") + 1])
                    response_path.write_text(
                        json.dumps(response, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                args = argparse.Namespace(
                    out_dir=out_dir,
                    codex="codex",
                    model="gpt-5.4-mini",
                    reasoning="low",
                    timeout_seconds=30,
                )
                identity = {
                    "requested_command": "codex",
                    "resolved_path": "codex",
                    "binary_sha256": "a" * 64,
                    "version_output": "test",
                }
                catalog = {"supported_reasoning_levels": ["low"]}
                with (
                    mock.patch.object(operator, "executable_identity", return_value=identity),
                    mock.patch.object(
                        operator,
                        "model_catalog_entry",
                        return_value=(catalog, "b" * 64),
                    ),
                    mock.patch.object(operator, "require_executable_hash"),
                    mock.patch.object(operator, "run_managed_capture", side_effect=fake_run),
                    self.assertRaisesRegex(ValueError, "Codex smoke failed"),
                ):
                    operator.smoke_test(args)

                record = json.loads((out_dir / "smoke.json").read_text(encoding="utf-8"))
                self.assertFalse(record["response_matches_expected"])
                self.assertEqual(
                    "response did not equal the requested smoke object",
                    record["response_validation_error"],
                )

    def test_staging_contains_exactly_allowed_files_and_excludes_source_extras(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            write_core(source)
            (source / "oracle.json").write_text("not allowed\n", encoding="utf-8")
            isolated = root / "isolated"
            hashes = operator.stage_trial(source, isolated)
            expected = {path.as_posix() for path in operator.CORE_FILES}
            actual = {
                path.relative_to(isolated).as_posix()
                for path in isolated.rglob("*")
                if path.is_file()
            }
            self.assertEqual(expected, set(hashes))
            self.assertEqual(expected, actual)
            self.assertFalse((isolated / "oracle.json").exists())

    def test_parent_directory_fsync_is_posix_fail_closed_and_skipped_on_windows(self) -> None:
        path = Path("synthetic") / "evidence.json"
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        with (
            mock.patch.object(operator.os, "open", return_value=91) as open_dir,
            mock.patch.object(operator.os, "fsync") as fsync,
            mock.patch.object(operator.os, "close") as close,
        ):
            operator.fsync_parent_directory(path, platform_name="posix")
        open_dir.assert_called_once_with(path.parent, flags)
        fsync.assert_called_once_with(91)
        close.assert_called_once_with(91)

        with (
            mock.patch.object(operator.os, "open", return_value=92),
            mock.patch.object(operator.os, "fsync", side_effect=OSError("disk failure")),
            mock.patch.object(operator.os, "close") as close,
            self.assertRaisesRegex(OSError, "disk failure"),
        ):
            operator.fsync_parent_directory(path, platform_name="posix")
        close.assert_called_once_with(92)

        with mock.patch.object(operator.os, "open") as open_dir:
            operator.fsync_parent_directory(path, platform_name="nt")
        open_dir.assert_not_called()

    def test_child_created_file_fsync_also_persists_parent_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "response.raw.txt"
            path.write_bytes(b"{}\n")
            with mock.patch.object(operator, "fsync_parent_directory") as parent_sync:
                operator.fsync_existing_file(path)
            parent_sync.assert_called_once_with(path)

    def test_execute_refuses_allocated_hash_mismatch_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            trial = trial_record(source)
            trial["trial_file_hashes"] = dict(trial["trial_file_hashes"])
            trial["trial_file_hashes"]["task.md"] = "0" * 64
            with mock.patch.object(operator.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "do not exactly match allocation"):
                    operator.execute_trial(
                        trial,
                        run_dir,
                        str(OPERATOR_PATH),
                        operator.sha256_file(OPERATOR_PATH),
                        "gpt-5.4-mini",
                        "low",
                        30,
                    )
            popen.assert_not_called()
            self.assertFalse((source / "execution.started.json").exists())
            self.assertFalse((source / "prompt.sent.txt").exists())

    def test_execute_rejects_executable_swap_before_launch_without_persistent_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            trial = trial_record(source)
            executable = root / "codex.exe"
            executable.write_bytes(b"binary-a")
            expected_hash = operator.sha256_file(executable)
            executable.write_bytes(b"binary-b")
            with (
                mock.patch.object(operator.subprocess, "Popen") as popen,
                self.assertRaisesRegex(ValueError, "hash changed"),
            ):
                operator.execute_trial(
                    trial,
                    run_dir,
                    str(executable),
                    expected_hash,
                    "gpt-5.4-mini",
                    "low",
                    30,
                )
            popen.assert_not_called()
            self.assertFalse((source / "execution.started.json").exists())
            self.assertFalse((source / "prompt.sent.txt").exists())

    def test_execute_rejects_junction_trial_directory_without_writing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            dispatch = run_dir / "dispatch"
            dispatch.mkdir(parents=True)
            outside = root / "outside-target"
            outside.mkdir()
            write_core(outside)
            trial = trial_record(outside, VALID_TRIAL_ID)
            self.create_junction(dispatch / VALID_TRIAL_ID, outside)
            with mock.patch.object(operator.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "link, junction, or reparse point"):
                    operator.execute_trial(
                        trial,
                        run_dir,
                        str(OPERATOR_PATH),
                        operator.sha256_file(OPERATOR_PATH),
                        "gpt-5.4-mini",
                        "low",
                        30,
                    )
            popen.assert_not_called()
            for name in (
                "prompt.sent.txt",
                "execution.started.json",
                "execution.json",
                "codex.stdout.jsonl",
                "codex.stderr.txt",
            ):
                self.assertFalse((outside / name).exists())

    def test_execute_rejects_trial_id_path_escape_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            (run_dir / "dispatch").mkdir(parents=True)
            outside = run_dir / "outside"
            outside.mkdir()
            write_core(outside)
            trial = trial_record(outside, "../outside")
            with mock.patch.object(operator.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "unsafe trial_id"):
                    operator.execute_trial(
                        trial,
                        run_dir,
                        str(OPERATOR_PATH),
                        operator.sha256_file(OPERATOR_PATH),
                        "gpt-5.4-mini",
                        "low",
                        30,
                    )
            popen.assert_not_called()
            self.assertFalse((outside / "prompt.sent.txt").exists())

    def test_exact_hash_check_rejects_missing_and_unexpected_paths(self) -> None:
        actual = {"task.md": "a" * 64, "packet.json": "b" * 64}
        with self.assertRaisesRegex(ValueError, r"missing=\['response.schema.json'\]"):
            operator.require_allocated_hashes(
                "trial-test",
                {**actual, "response.schema.json": "c" * 64},
                actual,
            )
        with self.assertRaisesRegex(ValueError, r"unexpected=\['packet.json'\]"):
            operator.require_allocated_hashes(
                "trial-test",
                {"task.md": "a" * 64},
                actual,
            )

    def test_execute_rejects_legacy_response_json_without_reading_or_repairing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            trial = trial_record(source)
            (source / "response.json").write_text('{"repaired":true}\n', encoding="utf-8")
            with mock.patch.object(operator.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "output path already exists"):
                    operator.execute_trial(
                        trial,
                        run_dir,
                        str(OPERATOR_PATH),
                        operator.sha256_file(OPERATOR_PATH),
                        "gpt-5.4-mini",
                        "low",
                        30,
                    )
            popen.assert_not_called()
            self.assertEqual(
                '{"repaired":true}\n',
                (source / "response.json").read_text(encoding="utf-8"),
            )

    def test_execute_uses_fresh_isolated_cwd_and_external_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            (source / "allocation-copy.json").write_text("secret\n", encoding="utf-8")
            trial = trial_record(source)
            captured: dict[str, object] = {}
            lifecycle: list[str] = []
            parent_syncs: list[Path] = []

            class FakeProcess:
                returncode = 0

                def __init__(self, argv: list[str]) -> None:
                    self.argv = argv

                def communicate(self, payload: bytes | None = None, timeout: int | None = None) -> None:
                    lifecycle.append("communicate")
                    captured["prompt_bytes"] = payload
                    captured["timeout"] = timeout
                    response = Path(self.argv[self.argv.index("--output-last-message") + 1])
                    response.write_text('{"ok":true}\n', encoding="utf-8")

                def kill(self) -> None:  # pragma: no cover - timeout path is not used
                    raise AssertionError("unexpected timeout")

            class FakeContainment:
                def terminate(self, _: FakeProcess) -> None:
                    lifecycle.append("terminate")

                def wait_empty(self, _: float) -> None:
                    lifecycle.append("wait_empty")

                def close(self) -> None:
                    lifecycle.append("close")

            def fake_popen(argv: list[str], **kwargs: object) -> FakeProcess:
                cwd = Path(kwargs["cwd"])
                captured["cwd"] = cwd
                captured["argv"] = argv
                captured["stage_files"] = {
                    path.relative_to(cwd).as_posix()
                    for path in cwd.rglob("*")
                    if path.is_file()
                }
                captured["stdout_path"] = Path(kwargs["stdout"].name)
                captured["stderr_path"] = Path(kwargs["stderr"].name)
                captured["popen_kwargs"] = kwargs
                return FakeProcess(argv)

            def fake_launch(argv: list[str], **kwargs: object) -> operator.ManagedChild:
                owner = kwargs.pop("owner")
                controls, _ = operator.child_process_isolation()
                managed = operator.ManagedChild(
                    fake_popen(argv, **kwargs, **controls), FakeContainment()
                )
                owner.add(managed)
                return managed

            with (
                mock.patch.object(operator, "launch_managed", side_effect=fake_launch),
                mock.patch.object(
                    operator,
                    "fsync_parent_directory",
                    side_effect=lambda path: parent_syncs.append(path),
                ),
            ):
                result = operator.execute_trial(
                    trial,
                    run_dir,
                    str(OPERATOR_PATH),
                    operator.sha256_file(OPERATOR_PATH),
                    "gpt-5.4-mini",
                    "low",
                    30,
                )

            isolated = captured["cwd"]
            argv = captured["argv"]
            self.assertEqual({path.as_posix() for path in operator.CORE_FILES}, captured["stage_files"])
            self.assertNotEqual(source.resolve(), isolated)
            self.assertFalse(isolated.exists())
            self.assertEqual(str(isolated), argv[argv.index("--cd") + 1])
            self.assertEqual(
                str(isolated / "response.schema.json"),
                argv[argv.index("--output-schema") + 1],
            )
            self.assertEqual(
                (source / "response.raw.txt").resolve(strict=True),
                Path(argv[argv.index("--output-last-message") + 1]).resolve(strict=True),
            )
            self.assertEqual(
                (source / "codex.stdout.jsonl").resolve(strict=True),
                captured["stdout_path"].resolve(strict=True),
            )
            self.assertEqual(
                (source / "codex.stderr.txt").resolve(strict=True),
                captured["stderr_path"].resolve(strict=True),
            )
            self.assertTrue((source / "response.raw.txt").is_file())
            self.assertFalse((source / "response.json").exists())
            self.assertTrue((source / "execution.json").is_file())
            self.assertTrue(result["response_present"])
            self.assertIn(source / "codex.stdout.jsonl", parent_syncs)
            self.assertIn(source / "response.raw.txt", parent_syncs)
            self.assertEqual(["communicate", "terminate", "wait_empty", "close"], lifecycle)
            popen_kwargs = captured["popen_kwargs"]
            self.assertTrue(popen_kwargs["close_fds"])
            if os.name == "nt":
                self.assertEqual(
                    operator.WINDOWS_CREATE_NEW_PROCESS_GROUP
                    | operator.WINDOWS_CREATE_NO_WINDOW
                    | operator.WINDOWS_CREATE_SUSPENDED,
                    popen_kwargs["creationflags"],
                )
            else:
                self.assertTrue(popen_kwargs["start_new_session"])

    def test_wait_until_empty_polls_to_zero_and_fails_closed(self) -> None:
        now = [0.0]
        sleeps: list[float] = []
        counts = iter([2, 1, 0])

        def sleep_for(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        operator.wait_until_empty(
            lambda: next(counts),
            0.1,
            "fake tree",
            monotonic_fn=lambda: now[0],
            sleep_fn=sleep_for,
        )
        self.assertEqual(2, len(sleeps))

        now[0] = 0.0
        with self.assertRaisesRegex(operator.ProcessContainmentError, "still reports 1"):
            operator.wait_until_empty(
                lambda: 1,
                0.02,
                "fake tree",
                monotonic_fn=lambda: now[0],
                sleep_fn=sleep_for,
            )

        def broken_query() -> int:
            raise operator.ProcessContainmentError("query failed")

        with self.assertRaisesRegex(operator.ProcessContainmentError, "query failed"):
            operator.wait_until_empty(broken_query, 1.0, "fake tree")

    def test_child_isolation_records_atomic_windows_launch_and_honest_posix_scope(self) -> None:
        windows_controls, windows_record = operator.child_process_isolation("nt")
        self.assertNotEqual(
            0,
            windows_controls["creationflags"] & operator.WINDOWS_CREATE_SUSPENDED,
        )
        self.assertEqual(
            "windows_suspended_nested_job_kill_on_close",
            windows_record["mode"],
        )
        self.assertTrue(windows_record["create_suspended"])
        self.assertFalse(windows_record["target_execution_before_assignment"])
        self.assertIn("validate_primary_thread", windows_record["assignment_policy"])

        posix_controls, posix_record = operator.child_process_isolation("posix")
        self.assertTrue(posix_controls["start_new_session"])
        self.assertEqual(
            "posix_session_process_group_cooperative_cleanup",
            posix_record["mode"],
        )
        self.assertFalse(posix_record["escape_resistant"])
        self.assertEqual("original POSIX process group only", posix_record["containment_scope"])
        self.assertIn("setsid/setpgid", posix_record["trust_assumption"])

    def test_launch_managed_requires_a_preexisting_owner(self) -> None:
        with self.assertRaisesRegex(TypeError, "owner"):
            operator.launch_managed(
                ["codex", "exec"],
                platform_name="posix",
                popen_factory=lambda *_args, **_kwargs: self.fail(
                    "Popen must not run without an owner"
                ),
            )

    def test_deferred_launch_interrupt_replays_sigint_during_handler_restore(self) -> None:
        deferred = operator.DeferredLaunchInterrupts()
        signal_calls = 0

        def fake_signal(_signum: int, _handler: object) -> None:
            nonlocal signal_calls
            signal_calls += 1
            if signal_calls == 2:
                deferred._defer_sigint(operator.signal.SIGINT, None)

        with (
            mock.patch.object(
                operator.signal,
                "getsignal",
                return_value=operator.signal.default_int_handler,
            ),
            mock.patch.object(operator.signal, "signal", side_effect=fake_signal),
            self.assertRaises(KeyboardInterrupt),
        ):
            with deferred:
                pass
        self.assertEqual(2, signal_calls)

    def test_windows_popen_interrupt_and_job_close_failure_reports_containment(self) -> None:
        class FakeJob:
            def close(self) -> None:
                raise OSError("close failed")

        def interrupt_popen(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt("Popen interrupted")

        with self.assertRaisesRegex(
            operator.ProcessContainmentError,
            "owner publication.*close failed",
        ) as raised:
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=operator.ActiveProcessRegistry(),
                platform_name="nt",
                popen_factory=interrupt_popen,
                windows_job_factory=FakeJob,
            )
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)

    def test_job_factory_store_interrupt_is_deferred_until_owner_registration(self) -> None:
        events: list[str] = []
        owner_sizes: list[int] = []
        armed = False

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                self.returncode = -1
                events.append("root.wait")
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("assigned Job should terminate the root")

        class FakeJob:
            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")

            def terminate(self, _: FakeProcess) -> None:
                events.append("job.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        def create_job() -> FakeJob:
            nonlocal armed
            events.append("job.create")
            armed = True
            return FakeJob()

        owner = operator.ActiveProcessRegistry()

        def interrupt_job_store(frame: object, event: str, _arg: object) -> object:
            nonlocal armed
            if frame.f_code is operator.launch_managed.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and armed:
                    armed = False
                    owner_sizes.append(len(owner._children))
                    raise KeyboardInterrupt("deferred Job STORE_DEREF interrupt")
            return interrupt_job_store

        previous_trace = sys.gettrace()
        sys.settrace(interrupt_job_store)
        try:
            with self.assertRaisesRegex(KeyboardInterrupt, "Job STORE_DEREF"):
                operator.launch_managed(
                    ["codex.exe", "exec"],
                    owner=owner,
                    platform_name="nt",
                    popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                    windows_job_factory=create_job,
                    windows_resume_factory=lambda _process: events.append("thread.resume"),
                )
        finally:
            sys.settrace(previous_trace)
        self.assertEqual([1], owner_sizes)
        self.assertEqual(0, owner.cleanup_all())
        self.assertIn("job.close", events)

    def test_replayed_job_trace_after_popen_failure_can_close_empty_job(self) -> None:
        events: list[str] = []
        armed = False

        class FakeJob:
            def close(self) -> None:
                events.append("job.close")

        def create_job() -> FakeJob:
            nonlocal armed
            events.append("job.create")
            armed = True
            return FakeJob()

        def fail_popen(*_args: object, **_kwargs: object) -> None:
            events.append("popen.fail")
            raise RuntimeError("synthetic Popen failure")

        owner = operator.ActiveProcessRegistry()

        def interrupt_after_restore(frame: object, event: str, _arg: object) -> object:
            nonlocal armed
            if frame.f_code is operator.launch_managed.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and armed:
                    armed = False
                    raise KeyboardInterrupt("trace replay after failed Popen")
            return interrupt_after_restore

        previous_trace = sys.gettrace()
        sys.settrace(interrupt_after_restore)
        try:
            with self.assertRaisesRegex(KeyboardInterrupt, "trace replay"):
                operator.launch_managed(
                    ["codex.exe", "exec"],
                    owner=owner,
                    platform_name="nt",
                    popen_factory=fail_popen,
                    windows_job_factory=create_job,
                    windows_resume_factory=lambda _process: self.fail(
                        "failed Popen cannot resume"
                    ),
                )
        finally:
            sys.settrace(previous_trace)
        operator.cleanup_registry_with_retry(owner)
        self.assertFalse(armed)
        self.assertEqual(["job.create", "popen.fail", "job.close"], events)
        self.assertEqual(0, owner.cleanup_all())

    def test_windows_assignment_interrupt_reraises_only_after_proven_cleanup(self) -> None:
        for interrupt in (
            KeyboardInterrupt("assignment interrupted"),
            SystemExit("assignment exited"),
        ):
            with self.subTest(interrupt=type(interrupt).__name__):
                events: list[str] = []

                class FakeProcess:
                    pid = 321
                    _handle = 654
                    returncode: int | None = None

                    def wait(self, timeout: float) -> int:
                        del timeout
                        events.append("root.wait")
                        self.returncode = -1
                        return self.returncode

                    def poll(self) -> int | None:
                        return self.returncode

                    def kill(self) -> None:  # pragma: no cover - containment succeeds
                        raise AssertionError("unexpected root-only kill")

                class FakeJob:
                    def assign(self, _: FakeProcess) -> None:
                        events.append("job.assign")
                        raise interrupt

                    def terminate(self, _: FakeProcess) -> None:
                        events.append("job.terminate")

                    def wait_empty(self, _: float) -> None:
                        events.append("job.wait_empty")

                    def close(self) -> None:
                        events.append("job.close")

                owner = operator.ActiveProcessRegistry()
                with self.assertRaises(type(interrupt)):
                    operator.launch_managed(
                        ["codex.exe", "exec"],
                        owner=owner,
                        platform_name="nt",
                        popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                        windows_job_factory=FakeJob,
                    )
                self.assertEqual(
                    [
                        "job.assign",
                        "job.terminate",
                        "root.wait",
                        "job.wait_empty",
                        "job.close",
                    ],
                    events,
                )
                self.assertEqual(0, owner.cleanup_all())

    def test_launch_managed_windows_orders_assignment_and_full_cleanup(self) -> None:
        events: list[str] = []
        captured_kwargs: dict[str, object] = {}

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover - Job termination succeeds
                raise AssertionError("unexpected root-only kill")

        class FakeJob:
            def __init__(self) -> None:
                events.append("job.create")
                self.terminated = False

            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")

            def terminate(self, _: FakeProcess) -> None:
                if not self.terminated:
                    events.append("job.terminate")
                    self.terminated = True

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        def fake_popen(_: list[str], **kwargs: object) -> FakeProcess:
            events.append("popen")
            captured_kwargs.update(kwargs)
            return FakeProcess()

        def fake_resume(_: FakeProcess) -> None:
            events.append("thread.resume")

        class RecordingOwner(operator.ActiveProcessRegistry):
            def add(self, child: operator.ManagedChild) -> None:
                events.append("owner.add")
                super().add(child)

        owner = RecordingOwner()
        managed = operator.launch_managed(
            ["codex.exe", "exec"],
            owner=owner,
            platform_name="nt",
            popen_factory=fake_popen,
            windows_job_factory=FakeJob,
            windows_resume_factory=fake_resume,
            stdin=subprocess.PIPE,
        )
        self.assertEqual(
            ["owner.add", "job.create", "popen", "job.assign", "thread.resume"],
            events,
        )
        self.assertTrue(captured_kwargs["close_fds"])
        flags = captured_kwargs["creationflags"]
        self.assertEqual(
            operator.WINDOWS_CREATE_NEW_PROCESS_GROUP
            | operator.WINDOWS_CREATE_NO_WINDOW
            | operator.WINDOWS_CREATE_SUSPENDED,
            flags,
        )
        self.assertNotEqual(0, flags & operator.WINDOWS_CREATE_SUSPENDED)
        self.assertEqual(0, flags & 0x00000008)  # DETACHED_PROCESS
        self.assertEqual(0, flags & 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB

        managed.cleanup_tree(0.1)
        self.assertEqual(
            [
                "owner.add",
                "job.create",
                "popen",
                "job.assign",
                "thread.resume",
                "job.terminate",
                "root.wait",
                "job.wait_empty",
                "job.close",
            ],
            events,
        )
        owner.discard(managed)

    def test_launch_managed_resume_failure_drains_the_assigned_job(self) -> None:
        events: list[str] = []

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover - Job termination succeeds
                raise AssertionError("unexpected root-only kill")

        class FakeJob:
            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")

            def terminate(self, _: FakeProcess) -> None:
                events.append("job.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        def fail_resume(_: FakeProcess) -> None:
            events.append("thread.resume")
            raise operator.ProcessContainmentError("synthetic resume failure")

        with self.assertRaisesRegex(
            operator.ProcessContainmentError, "synthetic resume failure"
        ):
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=operator.ActiveProcessRegistry(),
                platform_name="nt",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                windows_job_factory=FakeJob,
                windows_resume_factory=fail_resume,
            )
        self.assertEqual(
            [
                "job.assign",
                "thread.resume",
                "job.terminate",
                "root.wait",
                "job.wait_empty",
                "job.close",
            ],
            events,
        )

    def test_launch_managed_interrupt_during_resume_cleans_up_then_reraises(self) -> None:
        events: list[str] = []

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover - Job termination succeeds
                raise AssertionError("unexpected root-only kill")

        class FakeJob:
            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")

            def terminate(self, _: FakeProcess) -> None:
                events.append("job.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        def interrupt_resume(_: FakeProcess) -> None:
            events.append("thread.resume")
            raise KeyboardInterrupt("synthetic interrupt")

        with self.assertRaisesRegex(KeyboardInterrupt, "synthetic interrupt"):
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=operator.ActiveProcessRegistry(),
                platform_name="nt",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                windows_job_factory=FakeJob,
                windows_resume_factory=interrupt_resume,
            )
        self.assertEqual(
            [
                "job.assign",
                "thread.resume",
                "job.terminate",
                "root.wait",
                "job.wait_empty",
                "job.close",
            ],
            events,
        )

    def test_resume_interrupt_with_failed_cleanup_raises_containment_and_retains(self) -> None:
        class FakeProcess:
            pid = 321
            _handle = 654
            returncode = None

        class FakeJob:
            def assign(self, _: FakeProcess) -> None:
                return

            def terminate(self, _: FakeProcess) -> None:
                raise OSError("job termination failed")

            def wait_empty(self, _: float) -> None:  # pragma: no cover
                raise AssertionError("empty proof cannot follow failed termination")

            def close(self) -> None:  # pragma: no cover
                raise AssertionError("unproven boundary must remain open")

        owner = operator.ActiveProcessRegistry()
        with self.assertRaisesRegex(
            operator.ProcessContainmentError, "launch cleanup remained unproven"
        ) as raised:
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=owner,
                platform_name="nt",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                windows_job_factory=FakeJob,
                windows_resume_factory=lambda _process: (_ for _ in ()).throw(
                    KeyboardInterrupt("resume interrupted")
                ),
            )
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)
        self.assertEqual(1, len(owner._children))

    def test_cancelling_owner_prevents_resume_and_discards_only_after_cleanup(self) -> None:
        events: list[str] = []

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("unexpected root-only kill")

        class FakeJob:
            terminated = False

            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")

            def terminate(self, _: FakeProcess) -> None:
                if not self.terminated:
                    events.append("job.terminate")
                    self.terminated = True

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        owner = operator.ActiveProcessRegistry()
        self.assertEqual(0, owner.terminate_all())
        with self.assertRaisesRegex(
            operator.ProcessContainmentError, "raced a child launch"
        ):
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=owner,
                platform_name="nt",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                windows_job_factory=FakeJob,
                windows_resume_factory=lambda _process: events.append("thread.resume"),
            )
        self.assertNotIn("thread.resume", events)
        self.assertEqual([], events)
        self.assertEqual(0, owner.cleanup_all())

    def test_cancellation_during_job_assignment_never_resumes_empty_terminated_job(self) -> None:
        events: list[str] = []
        assignment_entered = threading.Event()
        release_assignment = threading.Event()

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                self.returncode = -1
                events.append("root.wait")
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("assigned Job should terminate the root")

        class FakeJob:
            assigned = False
            terminated = False

            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign.enter")
                assignment_entered.set()
                if not release_assignment.wait(5):  # pragma: no cover
                    raise AssertionError("test did not release assignment")
                self.assigned = True
                events.append("job.assign.complete")

            def terminate(self, _: FakeProcess) -> None:
                if not self.assigned:
                    raise AssertionError("empty Job must never be terminated")
                if not self.terminated:
                    self.terminated = True
                    events.append("job.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")

        owner = operator.ActiveProcessRegistry()
        launch_failures: list[BaseException] = []
        cancellation_failures: list[BaseException] = []

        def launch() -> None:
            try:
                operator.launch_managed(
                    ["codex.exe", "exec"],
                    owner=owner,
                    platform_name="nt",
                    popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                    windows_job_factory=FakeJob,
                    windows_resume_factory=lambda _process: events.append("thread.resume"),
                )
            except BaseException as exc:
                launch_failures.append(exc)

        def cancel() -> None:
            try:
                owner.terminate_all()
            except BaseException as exc:
                cancellation_failures.append(exc)

        launcher = threading.Thread(target=launch)
        launcher.start()
        self.assertTrue(assignment_entered.wait(5))
        self.assertEqual(1, len(owner._children))
        guard = next(iter(owner._children))
        canceller = threading.Thread(target=cancel)
        canceller.start()
        self.assertTrue(guard._cancel_event.wait(5))
        release_assignment.set()
        launcher.join(5)
        canceller.join(5)
        self.assertFalse(launcher.is_alive())
        self.assertFalse(canceller.is_alive())
        self.assertEqual([], cancellation_failures)
        self.assertEqual(1, len(launch_failures))
        self.assertIsInstance(launch_failures[0], operator.ProcessContainmentError)
        self.assertNotIn("thread.resume", events)
        self.assertLess(events.index("job.assign.complete"), events.index("job.terminate"))
        self.assertEqual(0, owner.cleanup_all())

    @unittest.skipUnless(os.name == "nt", "requires Win32 process and Job APIs")
    def test_real_windows_suspended_launch_assigns_resumes_and_cleans_up(self) -> None:
        owner = operator.ActiveProcessRegistry()
        managed = operator.launch_managed(
            [operator.sys.executable, "-c", "print('atomic-launch-ok')"],
            owner=owner,
            platform_name="nt",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = managed.process.communicate(timeout=10)
            self.assertEqual(0, managed.process.returncode)
            self.assertEqual("atomic-launch-ok", stdout.strip())
            self.assertEqual("", stderr)
        finally:
            managed.cleanup_tree(1.0)
            owner.discard(managed)

    @unittest.skipUnless(os.name == "nt", "requires Win32 process and Job APIs")
    def test_real_windows_popen_store_interrupt_cleans_assigned_suspended_root(self) -> None:
        armed = False
        captured: list[object] = []

        class NoDeferredHooks:
            def __enter__(self) -> NoDeferredHooks:
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        original_publish = operator.popen_and_publish

        def publish_then_arm(*args: object, **kwargs: object) -> object:
            nonlocal armed
            process = original_publish(*args, **kwargs)
            captured.append(process)
            armed = True
            return process

        def interrupt_outer_store(frame: object, event: str, _arg: object) -> object:
            nonlocal armed
            if frame.f_code is operator.launch_managed.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and armed:
                    armed = False
                    raise KeyboardInterrupt("real Popen return before STORE_FAST")
            return interrupt_outer_store

        owner = operator.ActiveProcessRegistry()
        previous_trace = sys.gettrace()
        sys.settrace(interrupt_outer_store)
        try:
            with (
                mock.patch.object(operator, "DeferredLaunchInterrupts", NoDeferredHooks),
                mock.patch.object(
                    operator, "popen_and_publish", side_effect=publish_then_arm
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "real Popen return"),
            ):
                operator.launch_managed(
                    [sys.executable, "-c", "print('MUST_NOT_RUN')"],
                    owner=owner,
                    platform_name="nt",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    windows_resume_factory=lambda _process: self.fail(
                        "suspended process must not resume"
                    ),
                )
        finally:
            sys.settrace(previous_trace)
        self.assertFalse(armed)
        self.assertEqual(1, len(captured))
        process = captured[0]
        self.assertIsNotNone(process.poll())
        self.assertEqual("", process.stdout.read())
        process.stdout.close()
        process.stderr.close()
        self.assertEqual(0, owner.cleanup_all())

    @unittest.skipUnless(os.name == "nt", "requires Win32 nested-Job semantics")
    def test_real_windows_timeout_kills_spawned_descendant_before_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "descendant-started.txt"
            escaped = root / "descendant-escaped.txt"
            child_code = (
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8'); "
                "time.sleep(1.5); "
                "pathlib.Path(sys.argv[2]).write_text('escaped', encoding='utf-8'); "
                "time.sleep(30)"
            )
            parent_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]]); "
                "time.sleep(30)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                operator.run_managed_capture(
                    [
                        sys.executable,
                        "-c",
                        parent_code,
                        child_code,
                        str(ready),
                        str(escaped),
                    ],
                    timeout_seconds=0.75,
                )
            self.assertTrue(ready.is_file(), "descendant did not start before timeout")
            time.sleep(1.25)
            self.assertFalse(escaped.exists(), "descendant escaped the Windows Job")

    def test_launch_managed_assignment_interrupt_retains_unproven_boundary(self) -> None:
        events: list[str] = []

        class FakeProcess:
            pid = 321
            _handle = 654
            returncode = None

            def poll(self) -> None:
                events.append("root.poll")
                return None

            def kill(self) -> None:
                events.append("root.kill")
                raise OSError("kill failed")

            def wait(self, timeout: float) -> None:
                events.append("root.wait")
                raise subprocess.TimeoutExpired("codex.exe", timeout)

        class FakeJob:
            def assign(self, _: FakeProcess) -> None:
                events.append("job.assign")
                raise KeyboardInterrupt("assignment interrupted")

            def terminate(self, _: FakeProcess) -> None:
                events.append("job.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("job.wait_empty")

            def close(self) -> None:
                events.append("job.close")
                raise operator.ProcessContainmentError("close failed")

        owner = operator.ActiveProcessRegistry()
        with self.assertRaisesRegex(
            operator.ProcessContainmentError, "launch cleanup remained unproven"
        ) as raised:
            operator.launch_managed(
                ["codex.exe", "exec"],
                owner=owner,
                platform_name="nt",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                windows_job_factory=FakeJob,
            )
        self.assertEqual(
            [
                "job.assign",
                "job.terminate",
                "root.wait",
                "root.poll",
                "root.kill",
                "job.wait_empty",
                "job.terminate",
                "root.wait",
                "root.poll",
                "root.kill",
                "job.wait_empty",
            ],
            events,
        )
        self.assertEqual(1, len(owner._children))
        self.assertNotIn("job.close", events)
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)

    def test_posix_group_constructor_failure_uses_owned_native_fallback(self) -> None:
        events: list[str] = []

        class FakeProcess:
            pid = 4321
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("unexpected root-only kill")

        class FallbackGroup:
            def __init__(self, process_id: int) -> None:
                events.append(f"fallback.create:{process_id}")

            def terminate(self, _: FakeProcess) -> None:
                events.append("fallback.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("fallback.wait_empty")

            def close(self) -> None:
                events.append("fallback.close")

        def fail_group(_: int) -> None:
            events.append("custom.create")
            raise RuntimeError("group construction failed")

        owner = operator.ActiveProcessRegistry()
        with (
            mock.patch.object(operator, "PosixProcessGroup", FallbackGroup),
            self.assertRaisesRegex(
                operator.ProcessContainmentError, "group construction failed"
            ),
        ):
            operator.launch_managed(
                ["codex", "exec"],
                owner=owner,
                platform_name="posix",
                popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                posix_group_factory=fail_group,
            )
        self.assertEqual(
            [
                "custom.create",
                "fallback.create:4321",
                "fallback.terminate",
                "root.wait",
                "fallback.wait_empty",
                "fallback.close",
            ],
            events,
        )
        self.assertEqual(0, owner.cleanup_all())

    def test_capture_cleans_owner_if_interrupt_hits_before_store_fast(self) -> None:
        events: list[str] = []
        armed = False

        class FakeProcess:
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("unexpected root-only kill")

        class FakeContainment:
            def terminate(self, _: FakeProcess) -> None:
                events.append("containment.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("containment.wait_empty")

            def close(self) -> None:
                events.append("containment.close")

        def fake_launch(
            _argv: list[str], *, owner: operator.ActiveProcessRegistry, **_kwargs: object
        ) -> operator.ManagedChild:
            nonlocal armed
            managed = operator.ManagedChild(FakeProcess(), FakeContainment())
            owner.add(managed)
            armed = True
            return managed

        def interrupt_after_return(frame: object, event: str, _arg: object) -> object:
            nonlocal armed
            if frame.f_code is operator.run_managed_capture.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and armed:
                    armed = False
                    raise KeyboardInterrupt("between CALL and STORE_FAST")
            return interrupt_after_return

        previous_trace = sys.gettrace()
        sys.settrace(interrupt_after_return)
        try:
            with (
                mock.patch.object(operator, "launch_managed", side_effect=fake_launch),
                self.assertRaisesRegex(KeyboardInterrupt, "CALL and STORE_FAST"),
            ):
                operator.run_managed_capture(["codex", "--version"], timeout_seconds=1)
        finally:
            sys.settrace(previous_trace)
        self.assertFalse(armed)
        self.assertEqual(
            [
                "containment.terminate",
                "root.wait",
                "containment.wait_empty",
                "containment.close",
            ],
            events,
        )

    def test_launch_guard_owns_popen_result_before_outer_store_fast(self) -> None:
        events: list[str] = []
        armed = False

        class NoDeferredHooks:
            def __enter__(self) -> NoDeferredHooks:
                return self

            def __exit__(self, *_args: object) -> bool:
                return False

        class FakeProcess:
            pid = 4321
            returncode: int | None = None

            def wait(self, timeout: float) -> int:
                del timeout
                events.append("root.wait")
                self.returncode = -1
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:  # pragma: no cover
                raise AssertionError("group termination should reap the root")

        class FakeGroup:
            def __init__(self, process_id: int) -> None:
                events.append(f"group.create:{process_id}")

            def terminate(self, _: FakeProcess) -> None:
                events.append("group.terminate")

            def wait_empty(self, _: float) -> None:
                events.append("group.wait_empty")

            def close(self) -> None:
                events.append("group.close")

        original_publish = operator.popen_and_publish

        def publish_then_arm(*args: object, **kwargs: object) -> object:
            nonlocal armed
            process = original_publish(*args, **kwargs)
            armed = True
            return process

        def interrupt_outer_store(frame: object, event: str, _arg: object) -> object:
            nonlocal armed
            if frame.f_code is operator.launch_managed.__code__:
                frame.f_trace_opcodes = True
                if event == "opcode" and armed:
                    armed = False
                    raise KeyboardInterrupt("Popen return before outer STORE_FAST")
            return interrupt_outer_store

        owner = operator.ActiveProcessRegistry()
        previous_trace = sys.gettrace()
        sys.settrace(interrupt_outer_store)
        try:
            with (
                mock.patch.object(operator, "DeferredLaunchInterrupts", NoDeferredHooks),
                mock.patch.object(
                    operator, "popen_and_publish", side_effect=publish_then_arm
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "outer STORE_FAST"),
            ):
                operator.launch_managed(
                    ["codex", "exec"],
                    owner=owner,
                    platform_name="posix",
                    popen_factory=lambda *_args, **_kwargs: FakeProcess(),
                    posix_group_factory=FakeGroup,
                )
        finally:
            sys.settrace(previous_trace)
        self.assertFalse(armed)
        self.assertEqual(
            [
                "group.create:4321",
                "group.terminate",
                "root.wait",
                "group.wait_empty",
                "group.close",
            ],
            events,
        )
        self.assertEqual(0, owner.cleanup_all())

    def test_cleanup_recomputes_root_reap_after_containment_termination(self) -> None:
        events: list[str] = []

        class Process:
            returncode: int | None = None

        process = Process()

        class Containment:
            def terminate(self, _: Process) -> None:
                events.append("terminate")
                process.returncode = -1

            def wait_empty(self, _: float) -> None:
                events.append("wait_empty")

            def close(self) -> None:
                events.append("close")

        managed = operator.ManagedChild(process, Containment())
        managed.cleanup_tree(0.1)
        managed.cleanup_tree(0.1)
        self.assertEqual(["terminate", "wait_empty", "close"], events)

    def test_managed_cleanup_failures_leave_unproven_boundaries_open_for_retry(self) -> None:
        class ExitedProcess:
            returncode = 0

        for failing_phase, expected in (
            ("terminate", ["terminate"]),
            ("wait_empty", ["terminate", "wait_empty"]),
            ("close", ["terminate", "wait_empty", "close"]),
        ):
            with self.subTest(failing_phase=failing_phase):
                events: list[str] = []

                class FakeContainment:
                    def terminate(self, _: ExitedProcess) -> None:
                        events.append("terminate")
                        if failing_phase == "terminate":
                            raise operator.ProcessContainmentError("terminate failed")

                    def wait_empty(self, _: float) -> None:
                        events.append("wait_empty")
                        if failing_phase == "wait_empty":
                            raise operator.ProcessContainmentError("wait failed")

                    def close(self) -> None:
                        events.append("close")
                        if failing_phase == "close":
                            raise operator.ProcessContainmentError("close failed")

                managed = operator.ManagedChild(ExitedProcess(), FakeContainment())
                with self.assertRaisesRegex(
                    operator.ProcessContainmentError, "cleanup could not be proven"
                ):
                    managed.cleanup_tree(0.0)
                self.assertEqual(expected, events)

    def test_managed_cleanup_leaves_boundary_open_when_root_cannot_be_reaped(self) -> None:
        events: list[str] = []

        class StuckProcess:
            returncode = None

            def wait(self, timeout: float) -> None:
                events.append("root.wait")
                raise subprocess.TimeoutExpired("fake", timeout)

            def poll(self) -> None:
                events.append("root.poll")
                return None

            def kill(self) -> None:
                events.append("root.kill")

        class FakeContainment:
            def terminate(self, _: StuckProcess) -> None:
                events.append("terminate")

            def wait_empty(self, _: float) -> None:
                events.append("wait_empty")

            def close(self) -> None:
                events.append("close")

        managed = operator.ManagedChild(StuckProcess(), FakeContainment())
        with self.assertRaisesRegex(
            operator.ProcessContainmentError, "cleanup could not be proven"
        ):
            managed.cleanup_tree(0.0)
        self.assertEqual(
            [
                "terminate",
                "root.wait",
                "root.poll",
                "root.kill",
                "root.wait",
                "wait_empty",
            ],
            events,
        )

    def test_registry_retries_and_discards_worker_cleanup_failure_only_after_proof(self) -> None:
        events: list[str] = []

        class ExitedProcess:
            returncode = 0

        class TransientContainment:
            attempts = 0

            def terminate(self, _: ExitedProcess) -> None:
                self.attempts += 1
                events.append(f"terminate:{self.attempts}")
                if self.attempts == 1:
                    raise operator.ProcessContainmentError("transient termination failure")

            def wait_empty(self, _: float) -> None:
                events.append("wait_empty")

            def close(self) -> None:
                events.append("close")

        managed = operator.ManagedChild(ExitedProcess(), TransientContainment())
        registry = operator.ActiveProcessRegistry()
        registry.add(managed)
        with self.assertRaisesRegex(
            operator.ProcessContainmentError,
            "cleanup could not be proven",
        ):
            managed.cleanup_tree(0.0)
        self.assertEqual(1, registry.cleanup_all())
        self.assertEqual(0, registry.cleanup_all())
        self.assertEqual(
            ["terminate:1", "terminate:2", "wait_empty", "close"],
            events,
        )

    def test_unowned_cleanup_helper_retries_a_transient_termination_failure(self) -> None:
        events: list[str] = []

        class ExitedProcess:
            returncode = 0

        class TransientContainment:
            attempts = 0

            def terminate(self, _: ExitedProcess) -> None:
                self.attempts += 1
                events.append(f"terminate:{self.attempts}")
                if self.attempts == 1:
                    raise operator.ProcessContainmentError("transient failure")

            def wait_empty(self, _: float) -> None:
                events.append("wait_empty")

            def close(self) -> None:
                events.append("close")

        managed = operator.ManagedChild(ExitedProcess(), TransientContainment())
        operator.cleanup_managed_with_retry(managed)
        self.assertEqual(
            ["terminate:1", "terminate:2", "wait_empty", "close"],
            events,
        )

    def test_posix_group_suppresses_only_esrch_and_retries_eperm(self) -> None:
        group = operator.PosixProcessGroup(123)
        with mock.patch.object(
            operator.os, "killpg", side_effect=OSError(errno.ESRCH, "gone"), create=True
        ) as killpg:
            group.terminate(None)
            group.terminate(None)
        killpg.assert_called_once_with(123, operator.POSIX_SIGKILL)

        denied = operator.PosixProcessGroup(456)
        with mock.patch.object(
            operator.os,
            "killpg",
            side_effect=[PermissionError(errno.EPERM, "denied"), None],
            create=True,
        ) as killpg:
            with self.assertRaises(PermissionError):
                denied.terminate(None)
            denied.terminate(None)
        self.assertEqual(2, killpg.call_count)

    def test_active_registry_attempts_every_child_and_closes_late_launch_race(self) -> None:
        events: list[str] = []

        class FakeChild:
            def __init__(self, name: str, fail: bool = False) -> None:
                self.name = name
                self.fail = fail

            def terminate_tree(self) -> None:
                events.append(self.name)
                if self.fail:
                    raise operator.ProcessContainmentError(f"{self.name} failed")

        registry = operator.ActiveProcessRegistry()
        registry.add(FakeChild("good"))
        registry.add(FakeChild("bad", fail=True))
        with self.assertRaisesRegex(operator.ProcessContainmentError, "active process tree"):
            registry.terminate_all()
        self.assertEqual({"good", "bad"}, set(events))

        late = FakeChild("late")
        with self.assertRaisesRegex(operator.ProcessContainmentError, "raced a child launch"):
            registry.add(late)
        self.assertIn("late", events)

    def test_late_launch_termination_failure_remains_registered_for_cleanup_retry(self) -> None:
        events: list[str] = []

        class ExitedProcess:
            returncode = 0

        class TransientContainment:
            attempts = 0

            def terminate(self, _: ExitedProcess) -> None:
                self.attempts += 1
                events.append(f"terminate:{self.attempts}")
                if self.attempts == 1:
                    raise operator.ProcessContainmentError("first termination failed")

            def wait_empty(self, _: float) -> None:
                events.append("wait_empty")

            def close(self) -> None:
                events.append("close")

        registry = operator.ActiveProcessRegistry()
        self.assertEqual(0, registry.terminate_all())
        managed = operator.ManagedChild(ExitedProcess(), TransientContainment())
        with self.assertRaisesRegex(
            operator.ProcessContainmentError,
            "immediate termination request failed",
        ):
            registry.add(managed)
        self.assertEqual(1, registry.cleanup_all())
        self.assertEqual(0, registry.cleanup_all())
        self.assertEqual(
            ["terminate:1", "terminate:2", "wait_empty", "close"],
            events,
        )

    def test_execute_cancellation_race_reaps_and_closes_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            trial = trial_record(source)
            cancellation = operator.threading.Event()
            lifecycle: list[str] = []
            isolated: list[Path] = []

            class FakeProcess:
                returncode: int | None = None

                def communicate(self, *_args: object, **_kwargs: object) -> None:
                    raise AssertionError("cancelled child must not receive a prompt")

                def wait(self, timeout: float) -> int:
                    lifecycle.append("root.wait")
                    self.returncode = -1
                    return self.returncode

                def poll(self) -> int | None:
                    return self.returncode

                def kill(self) -> None:  # pragma: no cover - containment succeeds
                    raise AssertionError("unexpected root-only kill")

            class FakeContainment:
                def terminate(self, _: FakeProcess) -> None:
                    lifecycle.append("terminate")

                def wait_empty(self, _: float) -> None:
                    lifecycle.append("wait_empty")

                def close(self) -> None:
                    lifecycle.append("close")

            class CancellingRegistry(operator.ActiveProcessRegistry):
                def add(self, child: operator.ManagedChild) -> None:
                    super().add(child)
                    cancellation.set()

            def fake_launch(_argv: list[str], **kwargs: object) -> operator.ManagedChild:
                isolated.append(Path(kwargs["cwd"]))
                managed = operator.ManagedChild(FakeProcess(), FakeContainment())
                kwargs["owner"].add(managed)
                return managed

            with (
                mock.patch.object(operator, "launch_managed", side_effect=fake_launch),
                self.assertRaisesRegex(
                    operator.ProcessContainmentError, "cancellation raced a child launch"
                ),
            ):
                operator.execute_trial(
                    trial,
                    run_dir,
                    str(OPERATOR_PATH),
                    operator.sha256_file(OPERATOR_PATH),
                    "gpt-5.4-mini",
                    "low",
                    30,
                    cancellation,
                    CancellingRegistry(),
                )

            self.assertEqual(["terminate", "root.wait", "wait_empty", "close"], lifecycle)
            self.assertTrue(isolated)
            self.assertFalse(isolated[0].exists())
            self.assertFalse((source / "execution.json").exists())

    def test_execute_timeout_terminates_reaps_waits_empty_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            source = run_dir / "dispatch" / VALID_TRIAL_ID
            source.mkdir(parents=True)
            write_core(source)
            trial = trial_record(source)
            lifecycle: list[str] = []

            class FakeProcess:
                returncode: int | None = None

                def communicate(
                    self, payload: bytes | None = None, timeout: float | None = None
                ) -> None:
                    if payload is not None:
                        lifecycle.append("communicate.timeout")
                        raise subprocess.TimeoutExpired("codex.exe", timeout)
                    lifecycle.append("communicate.reap")
                    self.returncode = -1

            class FakeContainment:
                terminated = False

                def terminate(self, _: FakeProcess) -> None:
                    if not self.terminated:
                        lifecycle.append("terminate")
                        self.terminated = True

                def wait_empty(self, _: float) -> None:
                    lifecycle.append("wait_empty")

                def close(self) -> None:
                    lifecycle.append("close")

            def fake_launch(_argv: list[str], **kwargs: object) -> operator.ManagedChild:
                managed = operator.ManagedChild(FakeProcess(), FakeContainment())
                kwargs["owner"].add(managed)
                return managed

            with mock.patch.object(operator, "launch_managed", side_effect=fake_launch):
                result = operator.execute_trial(
                    trial,
                    run_dir,
                    str(OPERATOR_PATH),
                    operator.sha256_file(OPERATOR_PATH),
                    "gpt-5.4-mini",
                    "low",
                    1,
                )

            self.assertTrue(result["timed_out"])
            self.assertEqual(
                [
                    "communicate.timeout",
                    "terminate",
                    "communicate.reap",
                    "wait_empty",
                    "close",
                ],
                lifecycle,
            )

    def test_emit_json_line_tolerates_a_closed_output_stream(self) -> None:
        class ClosedStream:
            def write(self, _: str) -> int:
                raise BrokenPipeError("closed")

            def flush(self) -> None:  # pragma: no cover - write raises first
                raise AssertionError("unexpected flush")

        self.assertFalse(operator.emit_json_line({"event": "heartbeat"}, stream=ClosedStream()))

    def test_record_batch_abort_is_exclusive_and_requires_sealed_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            args = argparse.Namespace(command="run", run_dir=run_dir)
            self.assertIsNone(operator.record_batch_abort(args, RuntimeError("before config")))
            operator.write_json(run_dir / "operator-config.json", {"sealed": True})
            recorded = operator.record_batch_abort(args, RuntimeError("synthetic abort"))
            self.assertEqual(str(run_dir / "operator-abort.json"), recorded)
            payload = json.loads((run_dir / "operator-abort.json").read_text(encoding="utf-8"))
            self.assertEqual("RuntimeError", payload["exception_type"])
            self.assertIn("do not resume", payload["disposition"])
            self.assertIsNone(operator.record_batch_abort(args, RuntimeError("second")))
            self.assertEqual(
                "synthetic abort",
                json.loads((run_dir / "operator-abort.json").read_text(encoding="utf-8"))[
                    "exception_message"
                ],
            )
            cleanup_recorded = operator.record_batch_cleanup_failure(
                args,
                RuntimeError("synthetic abort"),
                [operator.ProcessContainmentError("job close failed")],
            )
            cleanup_path = run_dir / "operator-abort-cleanup.json"
            self.assertEqual(str(cleanup_path), cleanup_recorded)
            cleanup_payload = json.loads(cleanup_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "ProcessContainmentError",
                cleanup_payload["cleanup_failures"][0]["exception_type"],
            )
            self.assertEqual(
                operator.sha256_file(run_dir / "operator-abort.json"),
                cleanup_payload["operator_abort_sha256"],
            )
            self.assertIsNone(
                operator.record_batch_cleanup_failure(
                    args,
                    RuntimeError("replacement"),
                    [RuntimeError("replacement cleanup")],
                )
            )

    def test_batch_interrupt_records_abort_and_completes_cleanup_even_when_termination_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            trial_dir = run_dir / "dispatch" / VALID_TRIAL_ID
            trial_dir.mkdir(parents=True)
            allocation = {
                "seed": 20260830,
                "replicates": 1,
                "fixture_hashes": {},
                "skill_resource_hashes": {},
            }
            operator.write_json(run_dir / "allocation.private.json", allocation)
            trial = {"trial_id": VALID_TRIAL_ID, "trial_file_hashes": {}}
            events: list[str] = []

            class FakeFuture:
                def cancel(self) -> bool:
                    events.append("future.cancel")
                    return True

                def done(self) -> bool:
                    return False

                def cancelled(self) -> bool:  # pragma: no cover - done is false
                    return False

            class FakeExecutor:
                def __init__(self, max_workers: int) -> None:
                    events.append(f"executor.create:{max_workers}")

                def submit(self, *_args: object, **_kwargs: object) -> FakeFuture:
                    events.append("executor.submit")
                    return FakeFuture()

                def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                    events.append(f"executor.shutdown:{wait}:{cancel_futures}")

            class FailingRegistry:
                def terminate_all(self) -> None:
                    events.append("registry.terminate_all")
                    raise operator.ProcessContainmentError("synthetic termination failure")

                def cleanup_all(self) -> int:
                    events.append("registry.cleanup_all")
                    return 0

            args = argparse.Namespace(
                command="run",
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=1,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            real_abort = operator.record_batch_abort

            def recording_abort(
                abort_args: argparse.Namespace, exc: BaseException
            ) -> str | None:
                events.append("abort.write")
                return real_abort(abort_args, exc)

            with (
                mock.patch.object(
                    operator,
                    "validate_allocation",
                    return_value=([trial], [VALID_TRIAL_ID]),
                ),
                mock.patch.object(operator, "require_trial_directory", return_value=trial_dir),
                mock.patch.object(operator, "build_prompt", return_value=("prompt", {})),
                mock.patch.object(operator, "require_allocated_hashes"),
                mock.patch.object(operator, "require_fresh_trial_outputs"),
                mock.patch.object(
                    operator,
                    "executable_identity",
                    return_value={
                        "resolved_path": "codex",
                        "binary_sha256": "a" * 64,
                        "request_seed_options": [],
                    },
                ),
                mock.patch.object(
                    operator,
                    "model_catalog_entry",
                    return_value=({"supported_reasoning_levels": ["low"]}, "b" * 64),
                ),
                mock.patch.object(
                    operator,
                    "git_snapshot",
                    return_value={"head": "abc123", "status_short": []},
                ),
                mock.patch.object(operator, "require_executable_hash"),
                mock.patch.object(operator, "ActiveProcessRegistry", return_value=FailingRegistry()),
                mock.patch.object(
                    operator.concurrent.futures, "ThreadPoolExecutor", FakeExecutor
                ),
                mock.patch.object(
                    operator.concurrent.futures,
                    "wait",
                    side_effect=KeyboardInterrupt("synthetic interrupt"),
                ),
                mock.patch.object(
                    operator, "record_batch_abort", side_effect=recording_abort
                ),
                self.assertRaisesRegex(
                    operator.ProcessContainmentError,
                    "batch abort encountered containment cleanup failures",
                ),
            ):
                operator.run_batch(args)

            self.assertLess(events.index("abort.write"), events.index("registry.terminate_all"))
            self.assertLess(events.index("registry.terminate_all"), events.index("future.cancel"))
            self.assertLess(
                events.index("future.cancel"), events.index("executor.shutdown:True:True")
            )
            self.assertLess(
                events.index("executor.shutdown:True:True"), events.index("registry.cleanup_all")
            )
            self.assertTrue((run_dir / "operator-abort.json").is_file())
            self.assertTrue((run_dir / "operator-abort-cleanup.json").is_file())
            self.assertFalse((run_dir / "operator-summary.json").exists())

    def test_partial_initial_submission_failure_enters_full_abort_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            trial_dir = run_dir / "dispatch" / VALID_TRIAL_ID
            trial_dir.mkdir(parents=True)
            allocation = {
                "seed": 20260830,
                "replicates": 1,
                "fixture_hashes": {},
                "skill_resource_hashes": {},
            }
            operator.write_json(run_dir / "allocation.private.json", allocation)
            second_id = "trial-fedcba9876543210"
            trials = [
                {"trial_id": VALID_TRIAL_ID, "trial_file_hashes": {}},
                {"trial_id": second_id, "trial_file_hashes": {}},
            ]
            events: list[str] = []

            class FakeFuture:
                def cancel(self) -> bool:
                    events.append("future.cancel")
                    return True

            class FakeExecutor:
                def __init__(self, max_workers: int) -> None:
                    events.append(f"executor.create:{max_workers}")
                    self.submissions = 0

                def submit(self, *_args: object, **_kwargs: object) -> FakeFuture:
                    self.submissions += 1
                    events.append(f"executor.submit:{self.submissions}")
                    if self.submissions == 2:
                        raise KeyboardInterrupt("synthetic submit interrupt")
                    return FakeFuture()

                def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
                    events.append(f"executor.shutdown:{wait}:{cancel_futures}")

            class FakeRegistry:
                def terminate_all(self) -> int:
                    events.append("registry.terminate_all")
                    return 0

                def cleanup_all(self) -> int:
                    events.append("registry.cleanup_all")
                    return 0

            args = argparse.Namespace(
                command="run",
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=2,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            with (
                mock.patch.object(
                    operator,
                    "validate_allocation",
                    return_value=(trials, [VALID_TRIAL_ID, second_id]),
                ),
                mock.patch.object(operator, "require_trial_directory", return_value=trial_dir),
                mock.patch.object(operator, "build_prompt", return_value=("prompt", {})),
                mock.patch.object(operator, "require_allocated_hashes"),
                mock.patch.object(operator, "require_fresh_trial_outputs"),
                mock.patch.object(
                    operator,
                    "executable_identity",
                    return_value={
                        "resolved_path": "codex",
                        "binary_sha256": "a" * 64,
                        "request_seed_options": [],
                    },
                ),
                mock.patch.object(
                    operator,
                    "model_catalog_entry",
                    return_value=({"supported_reasoning_levels": ["low"]}, "b" * 64),
                ),
                mock.patch.object(
                    operator,
                    "git_snapshot",
                    return_value={"head": "abc123", "status_short": []},
                ),
                mock.patch.object(operator, "require_executable_hash"),
                mock.patch.object(operator, "ActiveProcessRegistry", return_value=FakeRegistry()),
                mock.patch.object(
                    operator.concurrent.futures,
                    "ThreadPoolExecutor",
                    FakeExecutor,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "synthetic submit interrupt"),
            ):
                operator.run_batch(args)

            self.assertEqual(
                [
                    "executor.create:2",
                    "executor.submit:1",
                    "executor.submit:2",
                    "registry.terminate_all",
                    "future.cancel",
                    "executor.shutdown:True:True",
                    "registry.cleanup_all",
                ],
                events,
            )
            self.assertTrue((run_dir / "operator-abort.json").is_file())
            self.assertFalse((run_dir / "operator-summary.json").exists())

    def test_batch_refuses_dirty_worktree_including_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            operator.write_json(
                run_dir / "allocation.private.json",
                {
                    "seed": 20260830,
                    "replicates": 5,
                    "fixture_hashes": {},
                    "skill_resource_hashes": {},
                    "trials": [],
                    "dispatch_order": [],
                },
            )
            args = argparse.Namespace(
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=1,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            with (
                mock.patch.object(
                    operator,
                    "executable_identity",
                    return_value={
                        "resolved_path": "codex",
                        "binary_sha256": "a" * 64,
                        "request_seed_options": [],
                    },
                ),
                mock.patch.object(
                    operator,
                    "model_catalog_entry",
                    return_value=({"supported_reasoning_levels": ["low"]}, "catalog"),
                ),
                mock.patch.object(
                    operator,
                    "git_snapshot",
                    return_value={"head": "abc123", "status_short": ["?? scratch.txt"]},
                ),
                mock.patch.object(operator, "require_executable_hash"),
            ):
                with self.assertRaisesRegex(ValueError, "worktree is dirty"):
                    operator.run_batch(args)
            self.assertFalse((run_dir / "operator-config.json").exists())

    def test_clean_batch_records_exact_repository_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            operator.write_json(
                run_dir / "allocation.private.json",
                {
                    "seed": 20260830,
                    "replicates": 5,
                    "fixture_hashes": {},
                    "skill_resource_hashes": {},
                    "trials": [],
                    "dispatch_order": [],
                },
            )
            args = argparse.Namespace(
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=1,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            with (
                mock.patch.object(
                    operator,
                    "executable_identity",
                    return_value={
                        "resolved_path": "codex",
                        "binary_sha256": "a" * 64,
                        "request_seed_options": [],
                    },
                ),
                mock.patch.object(
                    operator,
                    "model_catalog_entry",
                    return_value=({"supported_reasoning_levels": ["low"]}, "catalog"),
                ),
                mock.patch.object(
                    operator,
                    "git_snapshot",
                    return_value={"head": "abc123", "status_short": []},
                ),
                mock.patch.object(operator, "require_executable_hash"),
            ):
                summary = operator.run_batch(args)
            config = json.loads((run_dir / "operator-config.json").read_text(encoding="utf-8"))
            self.assertEqual("abc123", config["repository"]["head"])
            self.assertEqual([], config["repository"]["status_short"])
            self.assertEqual(0, summary["trial_count"])

    def test_batch_refuses_and_preserves_a_preexisting_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            operator.write_json(
                run_dir / "allocation.private.json",
                {
                    "seed": 20260830,
                    "replicates": 5,
                    "fixture_hashes": {},
                    "skill_resource_hashes": {},
                    "trials": [],
                    "dispatch_order": [],
                },
            )
            summary_path = run_dir / "operator-summary.json"
            summary_path.write_text("preserve\n", encoding="utf-8")
            args = argparse.Namespace(
                run_dir=run_dir,
                codex="codex",
                model="gpt-5.4-mini",
                reasoning="low",
                jobs=1,
                timeout_seconds=30,
                expected_commit="abc123",
                expected_allocation_seed=20260830,
            )
            with (
                mock.patch.object(
                    operator,
                    "executable_identity",
                    return_value={
                        "resolved_path": "codex",
                        "binary_sha256": "a" * 64,
                        "request_seed_options": [],
                    },
                ),
                mock.patch.object(
                    operator,
                    "model_catalog_entry",
                    return_value=({"supported_reasoning_levels": ["low"]}, "catalog"),
                ),
                mock.patch.object(
                    operator,
                    "git_snapshot",
                    return_value={"head": "abc123", "status_short": []},
                ),
                mock.patch.object(operator, "require_executable_hash"),
            ):
                with self.assertRaisesRegex(ValueError, "ineligible for resume"):
                    operator.run_batch(args)
            self.assertEqual("preserve\n", summary_path.read_text(encoding="utf-8"))
            self.assertFalse((run_dir / "operator-config.json").exists())

    def test_batch_refuses_raw_output_inside_repository_before_launch(self) -> None:
        args = argparse.Namespace(
            run_dir=operator.REPO_ROOT / "forbidden-run",
            codex="codex",
            model="gpt-5.4-mini",
            reasoning="low",
            jobs=1,
            timeout_seconds=30,
            expected_commit="unused",
            expected_allocation_seed=20260830,
        )
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            operator.run_batch(args)


if __name__ == "__main__":
    unittest.main()
