from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
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
                mock.patch.object(operator.subprocess, "run", side_effect=fake_run),
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
                mock.patch.object(operator.subprocess, "run", side_effect=fake_run),
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
                    mock.patch.object(operator.subprocess, "run", side_effect=fake_run),
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
                        "codex",
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
                    operator.execute_trial(trial, run_dir, "codex", "gpt-5.4-mini", "low", 30)
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
                    operator.execute_trial(trial, run_dir, "codex", "gpt-5.4-mini", "low", 30)
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
                        "codex",
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

            class FakeProcess:
                returncode = 0

                def __init__(self, argv: list[str]) -> None:
                    self.argv = argv

                def communicate(self, payload: bytes | None = None, timeout: int | None = None) -> None:
                    captured["prompt_bytes"] = payload
                    captured["timeout"] = timeout
                    response = Path(self.argv[self.argv.index("--output-last-message") + 1])
                    response.write_text('{"ok":true}\n', encoding="utf-8")

                def kill(self) -> None:  # pragma: no cover - timeout path is not used
                    raise AssertionError("unexpected timeout")

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
                return FakeProcess(argv)

            with mock.patch.object(operator.subprocess, "Popen", side_effect=fake_popen):
                result = operator.execute_trial(
                    trial,
                    run_dir,
                    "codex",
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
                    return_value={"resolved_path": "codex", "binary_sha256": "bin"},
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
                    return_value={"resolved_path": "codex", "binary_sha256": "bin"},
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
                    return_value={"resolved_path": "codex", "binary_sha256": "bin"},
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
            ):
                with self.assertRaisesRegex(ValueError, "refusing a second batch"):
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
