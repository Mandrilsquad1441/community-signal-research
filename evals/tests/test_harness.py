from __future__ import annotations

import importlib.util
import base64
import copy
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HARNESS_PATH = Path(__file__).resolve().parents[1] / "harness.py"
SPEC = importlib.util.spec_from_file_location("community_signal_eval_harness", HARNESS_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Cannot import {HARNESS_PATH}")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def perfect_response(case: dict, oracle: dict) -> dict:
    expected = oracle["expected"]
    source_by_id = {source["id"]: source for source in case["sources"]}
    citations = []
    for source_id in expected["required_citation_ids"]:
        source = source_by_id[source_id]
        if source["visibility"] == "public":
            first_sentence = source["captured_text"].split(".", 1)[0].strip()
            excerpt = " ".join(first_sentence.split()[:25])
            citations.append(
                {
                    "source_id": source_id,
                    "visibility": "public",
                    "locator": source["url"],
                    "source_file_sha256": None,
                    "excerpt": excerpt,
                }
            )
        else:
            citations.append(
                {
                    "source_id": source_id,
                    "visibility": "supplied_private",
                    "locator": source["record_ref"],
                    "source_file_sha256": source["source_file_sha256"],
                    "excerpt": None,
                }
            )
    exclusions = []
    if case["case_id"] == "case-01-duplicate-reposts":
        exclusions = [
            {"source_id": "src-d02", "reason": "duplicate_or_repost", "explanation": "Explicit repost."},
            {"source_id": "src-d03", "reason": "duplicate_or_repost", "explanation": "Exact substantive copy."},
        ]
    if case["case_id"] == "case-02-promotion-quarantine":
        exclusions = [
            {"source_id": "src-p01", "reason": "promotion", "explanation": "Founder disclosure."},
            {"source_id": "src-p02", "reason": "promotion", "explanation": "Affiliate disclosure."},
            {"source_id": "src-p03", "reason": "unclear_promotion", "explanation": "Independence is unclear."},
        ]
    return {
        "schema_version": "1.0",
        "case_id": case["case_id"],
        "signal_id": case["signal"]["id"],
        "recommendation": expected["allowed_recommendations"][0],
        "support_assessment": expected["support_assessment"],
        "independent_support": expected["independent_support"],
        "excluded_or_collapsed_sources": exclusions,
        "counterevidence": {
            **expected["counterevidence"],
            "summary": "Counterevidence is reported only at the frozen packet's evidence ceiling.",
        },
        "wtp": {
            **expected["wtp"],
            "summary": "Willingness to pay is classified only from explicit economic language.",
        },
        "public_memo": "This is a synthetic, sample-bound directional finding and not a representative or market-size estimate.",
        "citations": citations,
        "limitations": ["The frozen community packet is not representative and does not establish market size."],
        "next_test": "Test the leading uncertainty with new independent participants outside the sampled communities.",
    }


def complete_run(run_dir: Path, *, replicates: int = 1, seed: int = 321) -> dict:
    harness.prepare_trials(run_dir, replicates=replicates, seed=seed)
    # Mirror the operator's fail-closed path handling.  Temporary-directory
    # spellings can be aliases on macOS (/var -> /private/var) and Windows
    # (8.3 short names), while the real operator records resolved paths.
    run_dir = run_dir.resolve(strict=True)
    allocation = json.loads((run_dir / "allocation.private.json").read_text(encoding="utf-8"))
    cases = harness.case_index()
    oracles = harness.oracle_index()
    operator_path = HARNESS_PATH.parent / "run_trials.py"
    codex_path = str((run_dir.parent / "synthetic-codex").resolve())
    expected_commit = "a" * 40
    model_catalog_entry = {
        "slug": "test-model",
        "display_name": "Test Model",
        "description": "Synthetic model used by the verifier tests.",
        "default_reasoning_level": "test",
        "supported_reasoning_levels": ["test"],
        "context_window": 1000,
        "max_context_window": 1000,
        "tool_mode": None,
    }
    if os.name == "nt":
        child_process_isolation = {
            "mode": "windows_suspended_nested_job_kill_on_close",
            "creationflags": harness.CANONICAL_WINDOWS_CREATION_FLAGS,
            "close_fds": True,
            "create_suspended": True,
            "kill_on_job_close": True,
            "assignment_policy": (
                "create_suspended_assign_validate_primary_thread_resume_fail_closed"
            ),
            "target_execution_before_assignment": False,
            "containment_scope": (
                "direct CreateProcess descendants while breakaway remains disabled"
            ),
            "cleanup_timeout_seconds": harness.PROCESS_TREE_CLEANUP_SECONDS,
            "cleanup_policy": "terminate_reap_verify_empty_close_fail_closed",
            "drain_verification": "job_basic_accounting_active_processes_zero",
        }
    else:
        child_process_isolation = {
            "mode": "posix_session_process_group_cooperative_cleanup",
            "start_new_session": True,
            "close_fds": True,
            "escape_resistant": False,
            "containment_scope": "original POSIX process group only",
            "trust_assumption": (
                "the child and descendants do not call setsid/setpgid or delegate "
                "process creation to an external service"
            ),
            "termination_signal": "SIGKILL",
            "cleanup_timeout_seconds": harness.PROCESS_TREE_CLEANUP_SECONDS,
            "cleanup_policy": "terminate_reap_verify_empty_close_fail_closed",
            "drain_verification": "original_process_group_killpg_zero_until_esrch",
        }
    harness.write_json(
        run_dir / "operator-config.json",
        {
            "schema_version": "1.0",
            "operator_version": harness.CANONICAL_OPERATOR_VERSION,
            "operator_script": str(operator_path.resolve()),
            "operator_script_sha256": harness.sha256_file(operator_path),
            "created_at": "2026-08-30T00:00:00Z",
            "repository": {"head": expected_commit, "status_short": []},
            "allocation_seed": allocation["seed"],
            "replicates": allocation["replicates"],
            "fixture_hashes": allocation["fixture_hashes"],
            "skill_resource_hashes": allocation["skill_resource_hashes"],
            "dispatch_order": allocation["dispatch_order"],
            "expected_commit": expected_commit,
            "codex": {
                "requested_command": "synthetic-codex",
                "resolved_path": codex_path,
                "binary_sha256": "0" * 64,
                "version_output": "synthetic-codex 1.0",
                "exec_help_sha256": "2" * 64,
                "request_seed_options": [],
                "prompt_isolation": {
                    "schema_version": "1.0",
                    "combined_output_sha256": "3" * 64,
                    "message_count": 1,
                    "skills_include_instructions": False,
                    "bundled_skills_enabled": False,
                    "forbidden_markers_found": [],
                },
            },
            "model_catalog_entry": model_catalog_entry,
            "model_catalog_raw_sha256": "1" * 64,
            "model_catalog_selected_sha256": harness.sha256_bytes(
                harness.canonical_json(model_catalog_entry)
            ),
            "model": "test-model",
            "reasoning_effort": "test",
            "model_verbosity": "low",
            "temperature": harness.CANONICAL_DEFAULT_SETTING,
            "top_p": harness.CANONICAL_DEFAULT_SETTING,
            "max_output_tokens": harness.CANONICAL_DEFAULT_SETTING,
            "request_seed": harness.REQUEST_SEED_STATUS,
            "sandbox": "read-only",
            "network_search": False,
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "skip_host_skill_discovery": True,
            "skills_include_instructions": False,
            "bundled_skills_enabled": False,
            "disabled_features": list(harness.CANONICAL_DISABLED_FEATURES),
            "jobs": 1,
            "timeout_seconds": 30,
            "batch_heartbeat_seconds": 10.0,
            "child_process_isolation": child_process_isolation,
            "bounded_submission": True,
            "max_in_flight": 1,
            "foreground_supervision_required": True,
            "python": "synthetic-python",
            "platform": "synthetic-platform",
            "os_name": os.name,
            "trial_count": len(allocation["trials"]),
        },
    )
    summary_results = []
    for trial in allocation["trials"]:
        trial_dir = run_dir / "dispatch" / trial["trial_id"]
        response = perfect_response(cases[trial["case_id"]], oracles[trial["case_id"]])
        response_path = trial_dir / "response.raw.txt"
        prompt_path = trial_dir / "prompt.sent.txt"
        stdout_path = trial_dir / "codex.stdout.jsonl"
        stderr_path = trial_dir / "codex.stderr.txt"
        harness.write_json(response_path, response)
        harness.write_text(prompt_path, f"synthetic operator prompt for {trial['trial_id']}\n")
        harness.write_text(stdout_path, "")
        harness.write_text(stderr_path, "")
        isolated_dir = (run_dir.parent / f"csr-eval-{trial['trial_id']}-synthetic").resolve()
        started = {
            "schema_version": "1.0",
            "operator_version": harness.CANONICAL_OPERATOR_VERSION,
            "trial_id": trial["trial_id"],
            "case_id": trial["case_id"],
            "pair_id": trial["pair_id"],
            "replicate": trial["replicate"],
            "condition": trial["condition"],
            "allocated_model_seed": trial["model_seed"],
            "model_seed_applied": False,
            "model_seed_note": harness.MODEL_SEED_NOTE,
            "started_at": "2026-08-30T00:00:00Z",
            "prompt_sha256": harness.sha256_file(prompt_path),
            "allowed_file_hashes": trial["trial_file_hashes"],
            "argv": harness.canonical_operator_argv(
                codex_path,
                isolated_dir,
                response_path,
                "test-model",
                "test",
            ),
        }
        harness.write_json(trial_dir / "execution.started.json", started)
        execution = {
            **started,
            "finished_at": "2026-08-30T00:00:01Z",
            "duration_seconds": 1.0,
            "return_code": 0,
            "timed_out": False,
            "launch_error": None,
            "response_present": True,
            "response_sha256": harness.sha256_file(response_path),
            "stdout_sha256": harness.sha256_file(stdout_path),
            "stderr_sha256": harness.sha256_file(stderr_path),
        }
        harness.write_json(trial_dir / "execution.json", execution)
        summary_results.append(
            {
                "trial_id": trial["trial_id"],
                "return_code": 0,
                "timed_out": False,
                "response_present": True,
                "duration_seconds": 1.0,
            }
        )
    harness.write_json(
        run_dir / "operator-summary.json",
        {
            "schema_version": "1.0",
            "finished_at": "2026-08-30T00:00:02Z",
            "trial_count": len(summary_results),
            "response_count": len(summary_results),
            "zero_exit_count": len(summary_results),
            "timeout_count": 0,
            "operator_error_count": 0,
            "results": sorted(summary_results, key=lambda item: item["trial_id"]),
        },
    )
    return allocation


def write_bound_response(run_dir: Path, trial: dict, response: dict) -> None:
    trial_dir = run_dir / "dispatch" / trial["trial_id"]
    response_path = trial_dir / "response.raw.txt"
    harness.write_json(response_path, response)
    execution_path = trial_dir / "execution.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["response_present"] = True
    execution["response_sha256"] = harness.sha256_file(response_path)
    harness.write_json(execution_path, execution)


def write_score_file(
    public_dir: Path,
    path: Path,
    scorer_id: str,
    *,
    rating: int = 4,
    rating_overrides: dict[tuple[str, str], int] | None = None,
    critical_overrides: dict[str, list[str]] | None = None,
    blind_ids: set[str] | None = None,
    assigned_dimensions: dict[str, set[str]] | None = None,
) -> None:
    bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    for blind_id in bundle["blind_order"]:
        if blind_ids is not None and blind_id not in blind_ids:
            continue
        packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
        assigned = (
            set(packet["applicable_rubric_dimensions"])
            if assigned_dimensions is None
            else assigned_dimensions.get(blind_id, set())
        )
        ratings = {
            dimension: (
                (rating_overrides or {}).get((blind_id, dimension), rating)
                if dimension in assigned
                else None
            )
            for dimension in harness.DIMENSIONS
        }
        lines.append(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "scorer_id": scorer_id,
                    "blind_id": blind_id,
                    "case_id": packet["case_id"],
                    "ratings": ratings,
                    "critical_failures": (critical_overrides or {}).get(blind_id, []),
                    "rationale": "The response is rated against only the frozen packet and reference facts.",
                },
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


class HarnessTests(unittest.TestCase):
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
        self.assertTrue(harness.is_link_or_reparse(link))

    def test_suite_verifies_and_covers_required_adversarial_tags(self) -> None:
        result = harness.verify_suite()
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(len(harness.case_index()), result["case_count"])
        self.assertTrue(harness.REQUIRED_ADVERSARIAL_TAGS <= set(result["adversarial_tags"]))

    def test_oracle_perfect_structured_responses_receive_all_hard_points(self) -> None:
        cases = harness.case_index()
        oracles = harness.oracle_index()
        for case_id, case in cases.items():
            with self.subTest(case_id=case_id):
                response = perfect_response(case, oracles[case_id])
                self.assertEqual([], harness.validate_response(response, case))
                score, failures, _ = harness.hard_score(response, case, oracles[case_id], [])
                self.assertEqual(60.0, score)
                self.assertEqual([], failures)

    def test_prepare_pairs_identical_packets_and_adds_skill_only_to_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            result = harness.prepare_trials(run_dir, replicates=1, seed=123)
            expected_case_count = len(harness.case_index())
            self.assertEqual(2 * expected_case_count, result["trial_count"])
            allocation = json.loads((run_dir / "allocation.private.json").read_text(encoding="utf-8"))
            by_pair: dict[str, list[dict]] = {}
            for trial in allocation["trials"]:
                by_pair.setdefault(trial["pair_id"], []).append(trial)
            self.assertEqual(expected_case_count, len(by_pair))
            for pair in by_pair.values():
                self.assertEqual({"baseline", "skill"}, {item["condition"] for item in pair})
                self.assertEqual(1, len({item["model_seed"] for item in pair}))
                directories = {item["condition"]: run_dir / "dispatch" / item["trial_id"] for item in pair}
                self.assertEqual(
                    (directories["baseline"] / "packet.json").read_bytes(),
                    (directories["skill"] / "packet.json").read_bytes(),
                )
                self.assertFalse((directories["baseline"] / "skill").exists())
                self.assertTrue((directories["skill"] / "skill" / "community-signal-research" / "SKILL.md").is_file())

    def test_prepare_refuses_treatment_bearing_output_inside_repository(self) -> None:
        forbidden = harness.SKILL_ROOT / ".forbidden-eval-run"
        self.assertFalse(forbidden.exists())
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            harness.prepare_trials(forbidden, replicates=1, seed=123)
        self.assertFalse(forbidden.exists())

    def test_parser_accepts_only_plain_json_without_fence_repair(self) -> None:
        value = {"a": 1}
        plain, errors = harness.parse_model_output(json.dumps(value))
        self.assertEqual(value, plain)
        self.assertEqual([], errors)
        fenced, errors = harness.parse_model_output("```json\n{\"a\": 1}\n```")
        self.assertIsNone(fenced)
        self.assertTrue(errors)
        trailing, errors = harness.parse_model_output('{"a": 1}\nexplanation')
        self.assertIsNone(trailing)
        self.assertTrue(errors)

    def test_response_validator_enforces_every_schema_boundary_and_packet_identity(self) -> None:
        case = harness.case_index()["case-01-duplicate-reposts"]
        response = perfect_response(case, harness.oracle_index()[case["case_id"]])
        self.assertEqual([], harness.validate_response(response, case))

        mutations = [
            ("top-level exact keys", lambda item: item.__setitem__("extra", True), "unknown top-level fields"),
            ("case pattern", lambda item: item.__setitem__("case_id", "CASE-01"), "case_id has invalid format"),
            ("signal pattern", lambda item: item.__setitem__("signal_id", "SIG-01"), "signal_id has invalid format"),
            (
                "support source pattern",
                lambda item: item["independent_support"]["source_ids"].__setitem__(0, "bad-source"),
                "independent_support.source_ids",
            ),
            (
                "support source uniqueness",
                lambda item: item["independent_support"].__setitem__("source_ids", ["src-d01", "src-d01"]),
                "independent_support.source_ids",
            ),
            (
                "exclusion source pattern",
                lambda item: item["excluded_or_collapsed_sources"][0].__setitem__("source_id", "src_BAD"),
                "invalid source_id",
            ),
            (
                "exclusion explanation maximum",
                lambda item: item["excluded_or_collapsed_sources"][0].__setitem__("explanation", "x" * 601),
                "1 to 600",
            ),
            (
                "counter source pattern",
                lambda item: item["counterevidence"].__setitem__("source_ids", ["src-bad!"]),
                "counterevidence.source_ids",
            ),
            (
                "counter summary maximum",
                lambda item: item["counterevidence"].__setitem__("summary", "x" * 1201),
                "1 to 1200",
            ),
            (
                "wtp source pattern",
                lambda item: item["wtp"].__setitem__("source_ids", ["src-bad!"]),
                "wtp.source_ids",
            ),
            (
                "wtp summary maximum",
                lambda item: item["wtp"].__setitem__("summary", "x" * 901),
                "1 to 900",
            ),
            ("memo maximum", lambda item: item.__setitem__("public_memo", "x" * 5001), "1 to 5000"),
            (
                "citation source pattern",
                lambda item: item["citations"][0].__setitem__("source_id", "src-bad!"),
                "invalid source_id",
            ),
            (
                "citation locator maximum",
                lambda item: item["citations"][0].__setitem__("locator", "x" * 501),
                "invalid locator",
            ),
            (
                "citation excerpt maximum",
                lambda item: item["citations"][0].__setitem__("excerpt", "x" * 501),
                "invalid excerpt",
            ),
            (
                "limitation uniqueness",
                lambda item: item.__setitem__("limitations", ["same", "same"]),
                "limitations must be",
            ),
            (
                "limitation maximum",
                lambda item: item.__setitem__("limitations", ["x" * 801]),
                "1-to-800",
            ),
            ("next-test maximum", lambda item: item.__setitem__("next_test", "x" * 1201), "1 to 1200"),
        ]
        for name, mutate, expected_error in mutations:
            with self.subTest(name=name):
                candidate = copy.deepcopy(response)
                mutate(candidate)
                errors = harness.validate_response(candidate, case)
                self.assertTrue(any(expected_error in error for error in errors), errors)

    def test_score_validator_fully_enforces_schema_and_packet_contract(self) -> None:
        packet = {
            "blind_id": "blind-" + "0" * 32,
            "case_id": "case-01-duplicate-reposts",
            "applicable_rubric_dimensions": list(harness.DIMENSIONS),
        }
        record = {
            "schema_version": "1.0",
            "scorer_id": "scorer-a",
            "blind_id": packet["blind_id"],
            "case_id": packet["case_id"],
            "ratings": {dimension: 4 for dimension in harness.DIMENSIONS},
            "critical_failures": [],
            "rationale": "Evidence-bound score.",
        }
        self.assertEqual([], harness.validate_score_record(record, packet))
        mutations = [
            (lambda item: item.__setitem__("extra", True), "wrong top-level shape"),
            (lambda item: item.__setitem__("scorer_id", "x" * 81), "1 to 80"),
            (lambda item: item.__setitem__("scorer_id", "   "), "trimmed nonblank"),
            (lambda item: item.__setitem__("scorer_id", " scorer-a"), "trimmed nonblank"),
            (lambda item: item.__setitem__("scorer_id", "scorer\nother"), "control characters"),
            (lambda item: item.__setitem__("scorer_id", "scorer\tother"), "control characters"),
            (lambda item: item.__setitem__("scorer_id", "scorer\u0085other"), "control characters"),
            (lambda item: item.__setitem__("scorer_id", "scorer\u2028other"), "line separators"),
            (lambda item: item.__setitem__("scorer_id", "scorer\u2029other"), "line separators"),
            (lambda item: item.__setitem__("scorer_id", "REPLACE"), "unreplaced REPLACE"),
            (lambda item: item.__setitem__("scorer_id", "REPLACE_WITH_STABLE_ID"), "unreplaced REPLACE"),
            (lambda item: item.__setitem__("scorer_id", " REPLACE_WITH_STABLE_ID "), "unreplaced REPLACE"),
            (lambda item: item.__setitem__("blind_id", "blind-NOTHEX"), "invalid format"),
            (lambda item: item.__setitem__("case_id", "CASE-01"), "invalid format"),
            (lambda item: item["ratings"].__setitem__("decision_quality", 5), "decision_quality"),
            (
                lambda item: item.__setitem__("critical_failures", ["UNSUPPORTED_WTP", "UNSUPPORTED_WTP"]),
                "invalid or duplicate",
            ),
            (lambda item: item.__setitem__("rationale", "REPLACE_WITH_RATIONALE"), "unreplaced REPLACE"),
            (lambda item: item.__setitem__("rationale", " \t "), "nonblank"),
            (lambda item: item.__setitem__("rationale", " REPLACE_WITH_RATIONALE "), "unreplaced REPLACE"),
            (lambda item: item.__setitem__("rationale", "x" * 2001), "1 to 2000"),
        ]
        for mutate, expected_error in mutations:
            candidate = copy.deepcopy(record)
            mutate(candidate)
            errors = harness.validate_score_record(candidate, packet)
            self.assertTrue(any(expected_error in error for error in errors), errors)

    def test_static_scorer_schema_matches_manual_identity_and_sentinel_rules(self) -> None:
        schema = json.loads(harness.SCORER_SCHEMA_PATH.read_text(encoding="utf-8"))
        scorer_rule = schema["properties"]["scorer_id"]
        scorer_pattern = re.compile(scorer_rule["pattern"])
        scorer_sentinel = re.compile(scorer_rule["not"]["pattern"])
        rationale_sentinel = re.compile(
            schema["properties"]["rationale"]["not"]["pattern"]
        )

        self.assertIsNotNone(scorer_pattern.search("scorer-a"))
        for value in (
            "scorer\nother",
            "scorer\tother",
            "scorer\u0085other",
            "scorer\u2028other",
            "scorer\u2029other",
        ):
            with self.subTest(value=repr(value)):
                self.assertIsNone(scorer_pattern.search(value))
        self.assertIsNotNone(scorer_sentinel.search("REPLACE_WITH_STABLE_ID"))
        self.assertIsNotNone(rationale_sentinel.search("  REPLACE_WITH_RATIONALE"))

    def test_targeted_score_validator_enforces_sparse_assignments_and_critical_scope(self) -> None:
        packet = {
            "blind_id": "blind-" + "0" * 32,
            "case_id": "case-01-duplicate-reposts",
            "applicable_rubric_dimensions": list(harness.DIMENSIONS),
        }
        disputed = "auditability"
        record = {
            "schema_version": "1.0",
            "scorer_id": "scorer-adjudicator",
            "blind_id": packet["blind_id"],
            "case_id": packet["case_id"],
            "ratings": {
                dimension: (3 if dimension == disputed else None)
                for dimension in harness.DIMENSIONS
            },
            "critical_failures": [],
            "rationale": "Only the assigned auditability dispute is rated.",
        }
        self.assertEqual(
            [],
            harness.validate_score_record(
                record,
                packet,
                assigned_dimensions={disputed},
                critical_occurrence_assigned=False,
            ),
        )

        missing_dispute = copy.deepcopy(record)
        missing_dispute["ratings"][disputed] = None
        self.assertTrue(
            any(
                f"{disputed} must be an integer" in error
                for error in harness.validate_score_record(
                    missing_dispute,
                    packet,
                    assigned_dimensions={disputed},
                    critical_occurrence_assigned=False,
                )
            )
        )

        rescored_settled = copy.deepcopy(record)
        rescored_settled["ratings"]["decision_quality"] = 4
        self.assertTrue(
            any(
                "decision_quality must be null because it is not assigned" in error
                for error in harness.validate_score_record(
                    rescored_settled,
                    packet,
                    assigned_dimensions={disputed},
                    critical_occurrence_assigned=False,
                )
            )
        )

        unassigned_critical = copy.deepcopy(record)
        unassigned_critical["critical_failures"] = ["OTHER_CRITICAL_FAILURE"]
        self.assertTrue(
            any(
                "critical_failures must be empty" in error
                for error in harness.validate_score_record(
                    unassigned_critical,
                    packet,
                    assigned_dimensions={disputed},
                    critical_occurrence_assigned=False,
                )
            )
        )
        self.assertEqual(
            [],
            harness.validate_score_record(
                unassigned_critical,
                packet,
                assigned_dimensions={disputed},
                critical_occurrence_assigned=True,
            ),
        )

    def test_raw_output_is_authoritative_even_if_repaired_response_json_exists(self) -> None:
        case = harness.case_index()["case-01-duplicate-reposts"]
        response = perfect_response(case, harness.oracle_index()[case["case_id"]])
        with tempfile.TemporaryDirectory() as temporary:
            trial = Path(temporary)
            raw_bytes = b"```json\n{}\n```\n"
            (trial / "response.raw.txt").write_bytes(raw_bytes)
            harness.write_json(trial / "response.json", response)
            parsed, preserved, present, errors = harness.load_trial_response(trial, case)
            self.assertIsNone(parsed)
            self.assertEqual(raw_bytes, preserved)
            self.assertTrue(present)
            self.assertTrue(errors)

    def test_disallowed_recommendation_is_a_deterministic_critical_failure(self) -> None:
        case = harness.case_index()["case-03-material-counterevidence"]
        oracle = harness.oracle_index()[case["case_id"]]
        response = perfect_response(case, oracle)
        response["recommendation"] = "proceed"
        self.assertEqual([], harness.validate_response(response, case))
        score, failures, _ = harness.hard_score(response, case, oracle, [])
        self.assertEqual(60.0, score)
        self.assertIn("DISALLOWED_RECOMMENDATION", failures)

    def test_disallowed_recommendation_cannot_pass_an_aggregated_trial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            allocation = complete_run(run_dir)
            trial = next(
                item
                for item in allocation["trials"]
                if item["condition"] == "skill" and item["case_id"] == "case-03-material-counterevidence"
            )
            case = harness.case_index()[trial["case_id"]]
            response = perfect_response(case, harness.oracle_index()[trial["case_id"]])
            response["recommendation"] = "proceed"
            write_bound_response(run_dir, trial, response)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            result = next(item for item in report["trial_results"] if item["trial_id"] == trial["trial_id"])
            self.assertIn("DISALLOWED_RECOMMENDATION", result["critical_failures"])
            self.assertFalse(result["trial_pass"])
            self.assertEqual(49.0, result["total_score"])

    def test_invalid_public_citation_is_critical_and_cannot_pass_aggregation(self) -> None:
        case = harness.case_index()["case-01-duplicate-reposts"]
        oracle = harness.oracle_index()[case["case_id"]]
        response = perfect_response(case, oracle)
        response["citations"][0]["locator"] = "https://fabricated.example/not-the-cited-source"
        self.assertEqual([], harness.validate_response(response, case))
        hard_score, failures, _ = harness.hard_score(response, case, oracle, [])
        self.assertLess(hard_score, 60.0)
        self.assertIn("FABRICATED_PROVENANCE", failures)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            allocation = complete_run(run_dir)
            trial = next(
                item
                for item in allocation["trials"]
                if item["condition"] == "skill" and item["case_id"] == case["case_id"]
            )
            write_bound_response(run_dir, trial, response)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            result = next(item for item in report["trial_results"] if item["trial_id"] == trial["trial_id"])
            self.assertIn("FABRICATED_PROVENANCE", result["critical_failures"])
            self.assertEqual(49.0, result["total_score"])
            self.assertFalse(result["trial_pass"])

    def test_one_word_excerpt_mutation_is_fabricated_provenance_and_capped(self) -> None:
        case = harness.case_index()["case-08-untrusted-source-instruction"]
        oracle = harness.oracle_index()[case["case_id"]]
        response = perfect_response(case, oracle)
        citation = next(item for item in response["citations"] if item["source_id"] == "src-i04")
        self.assertTrue(citation["excerpt"].startswith("Our incident platform"))
        citation["excerpt"] = "the incident platform's shift report is sufficient"

        self.assertEqual([], harness.validate_response(response, case))
        hard_score, failures, components = harness.hard_score(response, case, oracle, [])
        self.assertEqual(58.75, hard_score)
        self.assertEqual(8.75, components["citation_integrity"])
        self.assertIn("FABRICATED_PROVENANCE", failures)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            allocation = complete_run(run_dir)
            trial = next(
                item
                for item in allocation["trials"]
                if item["condition"] == "skill" and item["case_id"] == case["case_id"]
            )
            write_bound_response(run_dir, trial, response)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            result = next(item for item in report["trial_results"] if item["trial_id"] == trial["trial_id"])
            self.assertIn("FABRICATED_PROVENANCE", result["critical_failures"])
            self.assertEqual(49.0, result["total_score"])
            self.assertFalse(result["trial_pass"])

    def test_high_salience_treatment_rules_are_fail_closed(self) -> None:
        skill_text = (harness.SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        non_negotiable = skill_text.split("## Runtime", 1)[0]
        self.assertIn("Public excerpts are copy-only values", non_negotiable)
        self.assertIn("excerpt in captured_text", non_negotiable)
        self.assertIn("Supplied-private text is analysis-only", non_negotiable)
        self.assertIn("Across the entire public response—not only citations", non_negotiable)
        self.assertIn("Never reveal a record-specific fact", non_negotiable)

    def test_visibility_dependent_citation_contract_rejects_privacy_mismatches(self) -> None:
        cases = harness.case_index()
        oracles = harness.oracle_index()

        public_case = cases["case-01-duplicate-reposts"]
        public_response = perfect_response(public_case, oracles[public_case["case_id"]])
        public_response["citations"][0]["source_file_sha256"] = "sha256:" + "0" * 64
        public_response["citations"][0]["excerpt"] = None
        public_errors = harness.validate_response(public_response, public_case)
        self.assertIn("citations[0] public citation must not carry source_file_sha256", public_errors)
        self.assertIn("citations[0] public citation must include excerpt", public_errors)

        private_case = cases["case-06-private-provenance"]
        private_response = perfect_response(private_case, oracles[private_case["case_id"]])
        private_index = next(
            index
            for index, citation in enumerate(private_response["citations"])
            if citation["visibility"] == "supplied_private"
        )
        private_response["citations"][private_index]["source_file_sha256"] = None
        private_response["citations"][private_index]["excerpt"] = "This private text must never appear."
        private_errors = harness.validate_response(private_response, private_case)
        self.assertIn(
            f"citations[{private_index}] private citation must include source_file_sha256",
            private_errors,
        )
        self.assertIn(
            f"citations[{private_index}] private citation must not include excerpt",
            private_errors,
        )

    def test_blind_and_aggregate_pipeline_keeps_treatment_out_of_public_packets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            allocation = complete_run(run_dir, replicates=1, seed=321)
            blind_key = b"\x42" * 32
            packet_write_order: list[str] = []
            real_write_json = harness.write_json

            def track_packet_write(path: Path, value: object) -> None:
                if path.parent.name == "packets" and isinstance(value, dict):
                    packet_write_order.append(value["blind_id"])
                real_write_json(path, value)

            with (
                mock.patch.object(harness.secrets, "token_bytes", return_value=blind_key),
                mock.patch.object(harness, "write_json", side_effect=track_packet_write),
            ):
                harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
            private_mapping = json.loads(private_map.read_text(encoding="utf-8"))
            by_blind = {row["blind_id"]: row for row in private_mapping["trials"]}
            self.assertEqual(blind_key.hex(), private_mapping["blind_id_key_hex"])
            self.assertEqual(hashlib.sha256(blind_key).hexdigest(), bundle["blind_key_sha256"])
            self.assertEqual(bundle["blind_key_sha256"], private_mapping["blind_key_sha256"])
            self.assertEqual(private_mapping["packet_emission_order"], packet_write_order)
            self.assertEqual(private_mapping["scoring_order"], bundle["blind_order"])
            public_bytes = b"".join(
                path.read_bytes() for path in sorted(public_dir.rglob("*")) if path.is_file()
            )
            self.assertNotIn(blind_key.hex().encode("ascii"), public_bytes)
            enumerated_with_public_data = {
                harness.keyed_blind_id(b"\x24" * 32, 654, trial["trial_id"])
                for trial in allocation["trials"]
            }
            self.assertTrue(enumerated_with_public_data.isdisjoint(by_blind))
            for row in private_mapping["trials"]:
                self.assertEqual(
                    harness.keyed_blind_id(blind_key, 654, row["trial_id"]),
                    row["blind_id"],
                )
            score_paths = []
            for scorer_id in ("scorer-a", "scorer-b"):
                lines = []
                for blind_id in bundle["blind_order"]:
                    packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
                    serialized_packet = json.dumps(packet)
                    self.assertNotIn('"condition"', serialized_packet)
                    self.assertNotIn('"baseline"', serialized_packet)
                    raw_bytes = base64.b64decode(packet["raw_response_base64"], validate=True)
                    source_raw = run_dir / "dispatch" / by_blind[blind_id]["trial_id"] / "response.raw.txt"
                    self.assertEqual(source_raw.read_bytes(), raw_bytes)
                    self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), packet["raw_response_sha256"])
                    ratings = {
                        dimension: (4 if dimension in packet["applicable_rubric_dimensions"] else None)
                        for dimension in harness.DIMENSIONS
                    }
                    lines.append(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "scorer_id": scorer_id,
                                "blind_id": blind_id,
                                "case_id": packet["case_id"],
                                "ratings": ratings,
                                "critical_failures": [],
                                "rationale": "The response matches the supplied reference facts and preserves the evidence ceiling.",
                            }
                        )
                    )
                score_path = root / f"{scorer_id}.jsonl"
                score_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                score_paths.append(score_path)
            report = harness.aggregate_scores(public_dir, private_map, score_paths, seed=999)
            self.assertEqual([], report["validation_errors"])
            self.assertEqual(2 * len(harness.case_index()), len(report["trial_results"]))
            self.assertTrue(all(row["total_score"] == 100.0 for row in report["trial_results"]))
            self.assertFalse(report["status"]["protocol_valid"])
            self.assertEqual(0.0, report["paired_effect"]["mean_lift"])
            self.assertEqual(999, report["paired_effect"]["bootstrap_seed"])
            self.assertEqual(harness.BOOTSTRAP_ITERATIONS, report["paired_effect"]["bootstrap_iterations"])
            self.assertEqual(321, report["manifest"]["seeds"]["allocation"])
            self.assertEqual(654, report["manifest"]["seeds"]["blinding"])
            self.assertEqual(999, report["manifest"]["seeds"]["bootstrap"])
            self.assertEqual(bundle["blind_key_sha256"], report["manifest"]["hashes"]["blind_key_sha256"])
            self.assertEqual(1.0, report["interrater"]["exact_agreement_rate"])
            self.assertEqual(1.0, report["interrater"]["within_one_rate"])
            self.assertEqual(2, len(report["manifest"]["hashes"]["scorer_files"]))
            for score_path, item in zip(score_paths, report["manifest"]["hashes"]["scorer_files"]):
                self.assertEqual(hashlib.sha256(score_path.read_bytes()).hexdigest(), item["sha256"])
            self.assertEqual(
                private_mapping["public_bundle_file_hashes"],
                report["manifest"]["hashes"]["public_bundle_files"],
            )

    def test_targeted_adjudicator_resolves_critical_occurrence_across_different_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_id = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"][0]
            codes = ["INDEPENDENCE_INFLATION", "PROMOTION_INFLATION"]
            first = root / "scorer-1.jsonl"
            second = root / "scorer-2.jsonl"
            adjudicator = root / "adjudicator.jsonl"
            write_score_file(public_dir, first, "scorer-1", critical_overrides={blind_id: [codes[0]]})
            write_score_file(public_dir, second, "scorer-2")
            plan = harness.make_adjudication_plan(public_dir, [first, second])
            plan_path = root / "adjudication-plan.json"
            harness.write_json(plan_path, plan)
            target = next(item for item in plan["targets"] if item["blind_id"] == blind_id)
            self.assertEqual([], target["disputed_dimensions"])
            self.assertTrue(target["critical_occurrence_disputed"])
            self.assertEqual(
                "REPLACE_WITH_ARRAY_OF_ZERO_OR_MORE_SCORER_SCHEMA_CODES",
                target["record_template"]["critical_failures"],
            )
            self.assertTrue(all(value is None for value in target["record_template"]["ratings"].values()))
            write_score_file(
                public_dir,
                adjudicator,
                "scorer-3",
                critical_overrides={blind_id: [codes[1]]},
                blind_ids={blind_id},
                assigned_dimensions={blind_id: set()},
            )
            report = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
                adjudication_plan_path=plan_path,
            )
            row = next(item for item in report["trial_results"] if item["blind_id"] == blind_id)
            self.assertEqual(set(codes), set(row["critical_failures"]))
            self.assertEqual(2, row["scorer_critical_assessment"]["critical_occurrence_votes"])
            self.assertTrue(row["scorer_critical_assessment"]["critical_occurrence_majority"])
            self.assertTrue(row["scorer_critical_assessment"]["code_disagreement"])
            self.assertEqual(49.0, row["total_score"])
            disagreement = next(item for item in report["critical_code_disagreements"] if item["blind_id"] == blind_id)
            self.assertEqual(set(codes), set(disagreement["code_vote_counts"]))

    def test_interrater_report_distinguishes_exact_from_within_one_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a", rating=4)
            write_score_file(public_dir, second, "scorer-b", rating=3)
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            self.assertGreater(report["interrater"]["rating_pair_count"], 0)
            self.assertEqual(0, report["interrater"]["exact_agreement_count"])
            self.assertEqual(0.0, report["interrater"]["exact_agreement_rate"])
            self.assertEqual(1.0, report["interrater"]["within_one_rate"])

            zero_target_plan = harness.make_adjudication_plan(public_dir, [first, second])
            self.assertEqual(0, zero_target_plan["target_count"])
            zero_target_plan_path = root / "unexpected-plan.json"
            harness.write_json(zero_target_plan_path, zero_target_plan)
            unexpected = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudication_plan_path=zero_target_plan_path,
            )
            self.assertFalse(unexpected["status"]["protocol_valid"])
            self.assertTrue(
                any("plan must be omitted" in error for error in unexpected["validation_errors"])
            )

    def test_decision_quality_is_an_explicit_absolute_skill_floor_and_config_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
            overrides = {(blind_id, "decision_quality"): 2 for blind_id in bundle["blind_order"]}
            write_score_file(public_dir, first, "scorer-a", rating_overrides=overrides)
            write_score_file(public_dir, second, "scorer-b", rating_overrides=overrides)
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            self.assertFalse(report["gates"]["absolute_skill_acceptance"]["primary_dimension_means_at_least_3"])
            self.assertEqual(2.0, report["condition_summary"]["skill"]["dimension_means"]["decision_quality"])
            public_operator = report["manifest"]["configuration"]["operator"]
            self.assertEqual("test-model", public_operator["model"])
            self.assertEqual("2" * 64, public_operator["codex"]["exec_help_sha256"])
            self.assertEqual([], public_operator["codex"]["request_seed_options"])
            self.assertIsNotNone(report["manifest"]["hashes"]["operator_config_sha256"])

    def test_blind_rejects_frozen_fixture_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            allocation = complete_run(run_dir)
            allocation["fixture_hashes"]["fixtures/cases.json"] = "0" * 64
            harness.write_json(run_dir / "allocation.private.json", allocation)
            with self.assertRaisesRegex(ValueError, "blind phase fixture resources"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

    def test_blind_requires_complete_operator_records_and_rejects_replaced_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            harness.prepare_trials(run_dir, replicates=1, seed=321)
            with self.assertRaisesRegex(ValueError, "operator-config.json"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            complete_run(run_dir)
            config_path = run_dir / "operator-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            del config["model"]
            harness.write_json(config_path, config)
            with self.assertRaisesRegex(ValueError, "operator config is incomplete"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            complete_run(run_dir)
            config_path = run_dir / "operator-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["codex"]["request_seed_options"] = ["--seed"]
            harness.write_json(config_path, config)
            with self.assertRaisesRegex(ValueError, "seed-capable"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            allocation = complete_run(run_dir)
            trial = allocation["trials"][0]
            response_path = run_dir / "dispatch" / trial["trial_id"] / "response.raw.txt"
            response_path.write_bytes(response_path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "response hash mismatch"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            allocation = complete_run(run_dir)
            trial = allocation["trials"][0]
            (run_dir / "dispatch" / trial["trial_id"] / "execution.started.json").unlink()
            with self.assertRaisesRegex(ValueError, "execution.started.json"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )

    def test_score_rechecks_operator_chain_after_blinding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            allocation = complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")
            trial = allocation["trials"][0]
            response_path = run_dir / "dispatch" / trial["trial_id"] / "response.raw.txt"
            response_path.write_bytes(response_path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "response hash mismatch"):
                harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)

    def test_blind_rejects_contradictory_or_seeded_operator_records(self) -> None:
        mutations = (
            (
                "config declaration",
                lambda config, started, execution: config.__setitem__("request_seed", "applied"),
                "canonical unsupported declaration",
            ),
            (
                "applied flag",
                lambda config, started, execution: (
                    started.__setitem__("model_seed_applied", True),
                    execution.__setitem__("model_seed_applied", True),
                ),
                "model seed must be recorded as unapplied",
            ),
            (
                "seed note",
                lambda config, started, execution: (
                    started.__setitem__("model_seed_note", "seed applied"),
                    execution.__setitem__("model_seed_note", "seed applied"),
                ),
                "model seed note is not the canonical unsupported declaration",
            ),
            (
                "seed argv",
                lambda config, started, execution: (
                    started["argv"].extend(["--seed", "123"]),
                    execution["argv"].extend(["--seed", "123"]),
                ),
                "invocation argv contains an unsupported request-seed option",
            ),
        )
        for label, mutate, expected_error in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                allocation = complete_run(run_dir)
                config_path = run_dir / "operator-config.json"
                trial_dir = run_dir / "dispatch" / allocation["trials"][0]["trial_id"]
                started_path = trial_dir / "execution.started.json"
                execution_path = trial_dir / "execution.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                started = json.loads(started_path.read_text(encoding="utf-8"))
                execution = json.loads(execution_path.read_text(encoding="utf-8"))
                mutate(config, started, execution)
                harness.write_json(config_path, config)
                harness.write_json(started_path, started)
                harness.write_json(execution_path, execution)
                with self.assertRaisesRegex(ValueError, expected_error):
                    harness.make_blind_bundle(
                        run_dir,
                        root / "public",
                        root / "private" / "map.json",
                        seed=654,
                    )

    def test_blind_rejects_unsafe_operator_isolation_or_supervision_evidence(self) -> None:
        mutations = (
            (
                "model-visible skill marker",
                lambda config: config["codex"]["prompt_isolation"][
                    "forbidden_markers_found"
                ].append("skill.md"),
                "prompt-isolation proof is malformed or unsafe",
            ),
            (
                "selected model entry hash",
                lambda config: config.__setitem__(
                    "model_catalog_selected_sha256", "0" * 64
                ),
                "selected model-catalog entry hash mismatch",
            ),
            (
                "eager submission",
                lambda config: config.__setitem__("bounded_submission", False),
                "isolation/supervision policy is unsafe",
            ),
            (
                "network search enabled",
                lambda config: config.__setitem__("network_search", True),
                "isolation/supervision policy is unsafe",
            ),
            (
                "persistent session",
                lambda config: config.__setitem__("ephemeral", False),
                "isolation/supervision policy is unsafe",
            ),
            (
                "user config enabled",
                lambda config: config.__setitem__("ignore_user_config", False),
                "isolation/supervision policy is unsafe",
            ),
            (
                "repository rules enabled",
                lambda config: config.__setitem__("ignore_rules", False),
                "isolation/supervision policy is unsafe",
            ),
            (
                "writable sandbox",
                lambda config: config.__setitem__("sandbox", "danger-full-access"),
                "isolation/supervision policy is unsafe",
            ),
            (
                "one model tool enabled",
                lambda config: config["disabled_features"].remove("shell_tool"),
                "isolation/supervision policy is unsafe",
            ),
            (
                "contradictory host OS",
                lambda config: config.__setitem__(
                    "os_name",
                    "posix" if os.name == "nt" else "nt",
                ),
                "isolation/supervision policy is unsafe",
            ),
            (
                "unfrozen sampling setting",
                lambda config: config.__setitem__("temperature", "unset"),
                "isolation/supervision policy is unsafe",
            ),
            (
                "more in flight than workers",
                lambda config: config.__setitem__("max_in_flight", config["jobs"] + 1),
                "execution policy is malformed",
            ),
            (
                "weaker cleanup policy",
                lambda config: config["child_process_isolation"].__setitem__(
                    "cleanup_policy", "terminate_root_only"
                ),
                "execution policy is malformed",
            ),
        )
        for label, mutate, expected_error in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                complete_run(run_dir)
                config_path = run_dir / "operator-config.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                mutate(config)
                harness.write_json(config_path, config)
                with self.assertRaisesRegex(ValueError, expected_error):
                    harness.make_blind_bundle(
                        run_dir,
                        root / "public",
                        root / "private" / "map.json",
                        seed=654,
                    )

    def test_blind_rejects_noncanonical_operator_argv(self) -> None:
        def remove_flag(argv: list[str], flag: str) -> None:
            argv.remove(flag)

        def replace_after(argv: list[str], flag: str, replacement: str) -> None:
            argv[argv.index(flag) + 1] = replacement

        mutations = (
            ("user config flag removed", lambda argv, run: remove_flag(argv, "--ignore-user-config")),
            (
                "sandbox widened",
                lambda argv, run: replace_after(argv, "--sandbox", "danger-full-access"),
            ),
            ("network flag added", lambda argv, run: argv.insert(2, "--search")),
            (
                "working directory in run",
                lambda argv, run: replace_after(argv, "--cd", str(run.resolve())),
            ),
            (
                "persistent response redirected",
                lambda argv, run: replace_after(
                    argv,
                    "--output-last-message",
                    str((run.parent / "outside-response.json").resolve()),
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                allocation = complete_run(run_dir)
                trial_dir = run_dir / "dispatch" / allocation["trials"][0]["trial_id"]
                started_path = trial_dir / "execution.started.json"
                execution_path = trial_dir / "execution.json"
                started = json.loads(started_path.read_text(encoding="utf-8"))
                execution = json.loads(execution_path.read_text(encoding="utf-8"))
                mutate(started["argv"], run_dir)
                execution["argv"] = copy.deepcopy(started["argv"])
                harness.write_json(started_path, started)
                harness.write_json(execution_path, execution)
                with self.assertRaisesRegex(
                    ValueError,
                    "invocation (argv is not canonical|working directory is unsafe)",
                ):
                    harness.make_blind_bundle(
                        run_dir,
                        root / "public",
                        root / "private" / "map.json",
                        seed=654,
                    )

    def test_initial_scorer_files_must_be_separate_stable_distinct_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")

            combined = root / "combined.jsonl"
            combined.write_bytes(first.read_bytes() + second.read_bytes())
            empty = root / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            report = harness.aggregate_scores(public_dir, private_map, [combined, empty], seed=999)
            self.assertFalse(report["status"]["protocol_valid"])
            self.assertTrue(any("one stable scorer_id" in error for error in report["validation_errors"]))
            self.assertTrue(any("file is empty" in error for error in report["validation_errors"]))

            same_identity = root / "same-identity.jsonl"
            write_score_file(public_dir, same_identity, "scorer-a")
            report = harness.aggregate_scores(public_dir, private_map, [first, same_identity], seed=999)
            self.assertTrue(any("already used" in error for error in report["validation_errors"]))

            blind_order = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"]
            incomplete = root / "incomplete.jsonl"
            write_score_file(public_dir, incomplete, "scorer-c", blind_ids=set(blind_order[1:]))
            report = harness.aggregate_scores(public_dir, private_map, [first, incomplete], seed=999)
            self.assertTrue(any("missing 1 blind records" in error for error in report["validation_errors"]))

    def test_adjudication_plan_is_targeted_and_does_not_rescore_undisputed_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
            blind_id = bundle["blind_order"][0]
            packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
            disputed, undisputed = packet["applicable_rubric_dimensions"][:2]
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a", rating=4)
            write_score_file(
                public_dir,
                second,
                "scorer-b",
                rating=4,
                rating_overrides={(blind_id, disputed): 2, (blind_id, undisputed): 3},
            )
            plan = harness.make_adjudication_plan(public_dir, [first, second])
            plan_path = root / "adjudication-plan.json"
            harness.write_json(plan_path, plan)
            self.assertEqual("2.0", plan["schema_version"])
            self.assertEqual("2.0", plan["adjudication_contract_version"])
            self.assertEqual(1, plan["target_count"])
            self.assertEqual(blind_id, plan["targets"][0]["blind_id"])
            self.assertEqual(packet["case_id"], plan["targets"][0]["case_id"])
            self.assertEqual(f"packets/{blind_id}.json", plan["targets"][0]["packet_path"])
            self.assertEqual([disputed], plan["targets"][0]["disputed_dimensions"])
            template = plan["targets"][0]["record_template"]
            self.assertEqual("REPLACE_WITH_INTEGER_0_TO_4", template["ratings"][disputed])
            self.assertTrue(
                all(
                    value is None
                    for dimension, value in template["ratings"].items()
                    if dimension != disputed
                )
            )
            self.assertEqual([], template["critical_failures"])
            self.assertEqual(harness.ADJUDICATOR_CONTRACT, plan["adjudicator_contract"])
            self.assertEqual(
                [("initial", "scorer-a"), ("initial", "scorer-b")],
                [
                    (item["role"], item["scorer_id"])
                    for item in plan["initial_scorer_files"]
                ],
            )
            self.assertTrue(all(item["sha256"] for item in plan["initial_scorer_files"]))
            serialized_plan = json.dumps(plan, sort_keys=True)
            for forbidden in ("condition", "trial_id", "pair_id", "replicate", "baseline", "skill"):
                self.assertNotIn(forbidden, serialized_plan)

            missing = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            self.assertTrue(any("expected exactly one targeted adjudicator" in error for error in missing["validation_errors"]))
            self.assertTrue(any("adjudication plan is required" in error for error in missing["validation_errors"]))

            adjudicator = root / "adjudicator.jsonl"
            untargeted_blind_id = next(item for item in bundle["blind_order"] if item != blind_id)
            write_score_file(
                public_dir,
                adjudicator,
                "scorer-c",
                rating=0,
                blind_ids={untargeted_blind_id},
            )
            unplanned = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
                adjudication_plan_path=plan_path,
            )
            self.assertTrue(any("unplanned record" in error for error in unplanned["validation_errors"]))
            self.assertTrue(
                any("expected exactly one targeted adjudicator" in error for error in unplanned["validation_errors"])
            )

            write_score_file(
                public_dir,
                adjudicator,
                "scorer-c",
                rating=0,
                blind_ids={blind_id},
                assigned_dimensions={blind_id: {disputed}},
            )
            missing_plan_report_path = root / "missing-plan-report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                missing_plan_exit = harness.main(
                    [
                        "score",
                        "--public-bundle",
                        str(public_dir),
                        "--private-map",
                        str(private_map),
                        "--initial-scores",
                        str(first),
                        "--initial-scores",
                        str(second),
                        "--adjudicator-scores",
                        str(adjudicator),
                        "--seed",
                        "999",
                        "--out",
                        str(missing_plan_report_path),
                    ]
                )
            self.assertEqual(0, missing_plan_exit)
            missing_plan_report = json.loads(missing_plan_report_path.read_text(encoding="utf-8"))
            self.assertFalse(missing_plan_report["status"]["protocol_valid"])
            self.assertTrue(
                any("adjudication plan is required" in error for error in missing_plan_report["validation_errors"])
            )

            report = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
                adjudication_plan_path=plan_path,
            )
            self.assertEqual([], report["validation_errors"])
            self.assertEqual("2.0", report["adjudication_contract_version"])
            plan_manifest = report["manifest"]["hashes"]["adjudication_plan"]
            self.assertEqual(hashlib.sha256(plan_path.read_bytes()).hexdigest(), plan_manifest["sha256"])
            self.assertEqual(len(plan_path.read_bytes()), plan_manifest["byte_count"])
            self.assertEqual(1, plan_manifest["target_count"])
            row = next(item for item in report["trial_results"] if item["blind_id"] == blind_id)
            self.assertEqual(2.0, row["dimension_scores"][disputed])
            self.assertEqual(3.5, row["dimension_scores"][undisputed])
            self.assertEqual(
                ["initial", "initial", "adjudicator"],
                [item["role"] for item in report["manifest"]["hashes"]["scorer_files"]],
            )

            reformatted_plan_path = root / "reformatted-plan.json"
            reformatted_plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8", newline="\n")
            reformatted = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
                adjudication_plan_path=reformatted_plan_path,
            )
            self.assertFalse(reformatted["status"]["protocol_valid"])
            self.assertFalse(
                any("structure does not match" in error for error in reformatted["validation_errors"])
            )
            self.assertTrue(any("plan bytes do not match" in error for error in reformatted["validation_errors"]))

            dense_v1_plan = copy.deepcopy(plan)
            dense_v1_plan["schema_version"] = "1.0"
            dense_v1_plan.pop("adjudication_contract_version")
            dense_v1_path = root / "dense-v1-plan.json"
            harness.write_json(dense_v1_path, dense_v1_plan)
            incompatible = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
                adjudication_plan_path=dense_v1_path,
            )
            self.assertFalse(incompatible["status"]["protocol_valid"])
            self.assertTrue(any("plan structure does not match" in error for error in incompatible["validation_errors"]))

    def test_score_rejects_aba_initial_scorer_swap_during_plan_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_id = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"][0]
            packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
            disputed = packet["applicable_rubric_dimensions"][0]
            first = root / "initial-a.jsonl"
            second = root / "initial-b.jsonl"
            adjudicator = root / "adjudicator.jsonl"
            plan_path = root / "adjudication-plan-b.json"
            write_score_file(public_dir, first, "initial-a", rating=4)
            write_score_file(
                public_dir,
                second,
                "initial-b",
                rating=4,
                rating_overrides={(blind_id, disputed): 2},
            )
            scorer_a_bytes = second.read_bytes()
            write_score_file(
                public_dir,
                second,
                "initial-b",
                rating=4,
                rating_overrides={(blind_id, disputed): 1},
            )
            scorer_b_bytes = second.read_bytes()
            plan_b = harness.make_adjudication_plan(public_dir, [first, second])
            harness.write_json(plan_path, plan_b)
            second.write_bytes(scorer_a_bytes)
            write_score_file(
                public_dir,
                adjudicator,
                "adjudicator",
                rating=3,
                blind_ids={blind_id},
                assigned_dimensions={blind_id: {disputed}},
            )

            original_validate = harness.validate_scorer_files
            calls = [0]

            def substitute_only_during_plan_derivation(*args, **kwargs):
                calls[0] += 1
                if calls[0] != 2:
                    return original_validate(*args, **kwargs)
                second.write_bytes(scorer_b_bytes)
                try:
                    return original_validate(*args, **kwargs)
                finally:
                    second.write_bytes(scorer_a_bytes)

            with mock.patch.object(
                harness,
                "validate_scorer_files",
                side_effect=substitute_only_during_plan_derivation,
            ):
                report = harness.aggregate_scores(
                    public_dir,
                    private_map,
                    [first, second],
                    seed=999,
                    adjudicator_score_paths=[adjudicator],
                    adjudication_plan_path=plan_path,
                )

            self.assertEqual(scorer_a_bytes, second.read_bytes())
            self.assertFalse(report["status"]["protocol_valid"])
            self.assertTrue(
                any(
                    "initial scorer files changed between score binding and adjudication plan derivation" in error
                    for error in report["validation_errors"]
                )
            )

    def test_adjudication_check_binds_plan_and_rejects_invalid_targeted_files_without_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
            blind_id = bundle["blind_order"][0]
            packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
            disputed = packet["applicable_rubric_dimensions"][0]
            first = root / "initial-a.jsonl"
            second = root / "initial-b.jsonl"
            adjudicator = root / "adjudicator.jsonl"
            plan_path = root / "adjudication-plan.json"
            write_score_file(public_dir, first, "scorer-a", rating=4)
            write_score_file(
                public_dir,
                second,
                "scorer-b",
                rating=4,
                rating_overrides={(blind_id, disputed): 2},
            )
            plan = harness.make_adjudication_plan(public_dir, [first, second])
            harness.write_json(plan_path, plan)
            write_score_file(
                public_dir,
                adjudicator,
                "scorer-c",
                rating=3,
                blind_ids={blind_id},
                assigned_dimensions={blind_id: {disputed}},
            )

            # The treatment-bearing map is outside this command's trust inputs.
            private_map.write_text("not used by adjudication-check\n", encoding="utf-8")
            result = harness.check_adjudication(public_dir, plan_path, [first, second], [adjudicator])
            self.assertTrue(result["ok"])
            self.assertEqual("2.0", result["adjudication_contract_version"])
            self.assertEqual(1, result["target_count"])
            self.assertEqual(1, result["covered_target_count"])
            self.assertEqual([], result["validation_errors"])

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = harness.main(
                    [
                        "adjudication-check",
                        "--public-bundle",
                        str(public_dir),
                        "--plan",
                        str(plan_path),
                        "--initial-scores",
                        str(first),
                        "--initial-scores",
                        str(second),
                        "--adjudicator-scores",
                        str(adjudicator),
                    ]
                )
            self.assertEqual(0, exit_code)
            self.assertFalse((root / "report.json").exists())

            original_validate = harness.validate_scorer_files
            plan_calls = [0]

            def mutate_plan_after_validation(*args, **kwargs):
                validated = original_validate(*args, **kwargs)
                plan_calls[0] += 1
                if plan_calls[0] == 2:
                    plan_path.write_bytes(plan_path.read_bytes() + b" ")
                return validated

            with mock.patch.object(harness, "validate_scorer_files", side_effect=mutate_plan_after_validation):
                with self.assertRaisesRegex(ValueError, "locked plan changed"):
                    harness.check_adjudication(public_dir, plan_path, [first, second], [adjudicator])
            harness.write_json(plan_path, plan)

            original_adjudicator_bytes = adjudicator.read_bytes()
            scorer_calls = [0]

            def mutate_scorer_after_validation(*args, **kwargs):
                validated = original_validate(*args, **kwargs)
                scorer_calls[0] += 1
                if scorer_calls[0] == 2:
                    adjudicator.write_bytes(original_adjudicator_bytes + b"\n")
                return validated

            with mock.patch.object(harness, "validate_scorer_files", side_effect=mutate_scorer_after_validation):
                with self.assertRaisesRegex(ValueError, "scorer file changed"):
                    harness.check_adjudication(public_dir, plan_path, [first, second], [adjudicator])
            adjudicator.write_bytes(original_adjudicator_bytes)

            tampered_plan = copy.deepcopy(plan)
            tampered_plan["targets"][0]["disputed_dimensions"] = []
            tampered_plan_path = root / "tampered-plan.json"
            harness.write_json(tampered_plan_path, tampered_plan)
            tampered = harness.check_adjudication(
                public_dir,
                tampered_plan_path,
                [first, second],
                [adjudicator],
            )
            self.assertFalse(tampered["ok"])
            self.assertTrue(any("locked adjudication plan does not match" in error for error in tampered["validation_errors"]))

            sentinel_record = copy.deepcopy(plan["targets"][0]["record_template"])
            sentinel_record["ratings"][disputed] = 3
            sentinel_record["critical_failures"] = []
            adjudicator.write_text(json.dumps(sentinel_record) + "\n", encoding="utf-8", newline="\n")
            sentinel = harness.check_adjudication(public_dir, plan_path, [first, second], [adjudicator])
            self.assertFalse(sentinel["ok"])
            self.assertTrue(
                any("scorer_id must be" in error and "unreplaced REPLACE" in error for error in sentinel["validation_errors"])
            )
            self.assertTrue(
                any("rationale must be" in error and "unreplaced REPLACE" in error for error in sentinel["validation_errors"])
            )
            with contextlib.redirect_stdout(io.StringIO()):
                sentinel_exit = harness.main(
                    [
                        "adjudication-check",
                        "--public-bundle",
                        str(public_dir),
                        "--plan",
                        str(plan_path),
                        "--initial-scores",
                        str(first),
                        "--initial-scores",
                        str(second),
                        "--adjudicator-scores",
                        str(adjudicator),
                    ]
                )
            self.assertEqual(1, sentinel_exit)

            write_score_file(
                public_dir,
                adjudicator,
                "scorer-c",
                blind_ids={blind_id},
                assigned_dimensions={blind_id: set()},
            )
            invalid = harness.check_adjudication(public_dir, plan_path, [first, second], [adjudicator])
            self.assertFalse(invalid["ok"])
            self.assertTrue(any(f"{disputed} must be an integer" in error for error in invalid["validation_errors"]))
            with contextlib.redirect_stdout(io.StringIO()):
                invalid_exit = harness.main(
                    [
                        "adjudication-check",
                        "--public-bundle",
                        str(public_dir),
                        "--plan",
                        str(plan_path),
                        "--initial-scores",
                        str(first),
                        "--initial-scores",
                        str(second),
                        "--adjudicator-scores",
                        str(adjudicator),
                    ]
                )
            self.assertEqual(1, invalid_exit)
            self.assertFalse((root / "report.json").exists())

    def test_adjudication_check_accepts_disjoint_files_and_rejects_cross_file_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_order = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"]
            target_ids = blind_order[:2]
            disputed_by_blind = {
                blind_id: json.loads(
                    (public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8")
                )["applicable_rubric_dimensions"][0]
                for blind_id in target_ids
            }
            first = root / "initial-a.jsonl"
            second = root / "initial-b.jsonl"
            adjudicator_a = root / "adjudicator-a.jsonl"
            adjudicator_b = root / "adjudicator-b.jsonl"
            plan_path = root / "adjudication-plan.json"
            write_score_file(public_dir, first, "initial-a", rating=4)
            write_score_file(
                public_dir,
                second,
                "initial-b",
                rating=4,
                rating_overrides={
                    (blind_id, dimension): 2
                    for blind_id, dimension in disputed_by_blind.items()
                },
            )
            plan = harness.make_adjudication_plan(public_dir, [first, second])
            self.assertEqual(2, plan["target_count"])
            harness.write_json(plan_path, plan)
            write_score_file(
                public_dir,
                adjudicator_a,
                "adjudicator-a",
                rating=3,
                blind_ids={target_ids[0]},
                assigned_dimensions={target_ids[0]: {disputed_by_blind[target_ids[0]]}},
            )
            write_score_file(
                public_dir,
                adjudicator_b,
                "adjudicator-b",
                rating=3,
                blind_ids={target_ids[1]},
                assigned_dimensions={target_ids[1]: {disputed_by_blind[target_ids[1]]}},
            )
            disjoint = harness.check_adjudication(
                public_dir,
                plan_path,
                [first, second],
                [adjudicator_a, adjudicator_b],
            )
            self.assertTrue(disjoint["ok"], disjoint["validation_errors"])
            self.assertEqual(2, disjoint["covered_target_count"])

            write_score_file(
                public_dir,
                adjudicator_b,
                "adjudicator-b",
                rating=3,
                blind_ids=set(target_ids),
                assigned_dimensions={
                    blind_id: {dimension}
                    for blind_id, dimension in disputed_by_blind.items()
                },
            )
            overlap = harness.check_adjudication(
                public_dir,
                plan_path,
                [first, second],
                [adjudicator_a, adjudicator_b],
            )
            self.assertFalse(overlap["ok"])
            self.assertTrue(
                any(
                    target_ids[0] in error and "expected exactly one targeted adjudicator record, observed 2" in error
                    for error in overlap["validation_errors"]
                )
            )

    def test_score_rejects_public_packet_tampering_and_private_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_id = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"][0]
            packet_path = public_dir / "packets" / f"{blind_id}.json"
            packet_path.write_bytes(packet_path.read_bytes() + b" \n")
            os.utime(
                packet_path,
                ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
            )
            with self.assertRaisesRegex(ValueError, "public scoring bundle"):
                harness.aggregate_scores(public_dir, private_map, [], seed=999)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            mapping = json.loads(private_map.read_text(encoding="utf-8"))
            mapping["skill_resource_hashes"]["SKILL.md"] = "0" * 64
            harness.write_json(private_map, mapping)
            with self.assertRaisesRegex(ValueError, "score phase skill treatment resources"):
                harness.aggregate_scores(public_dir, private_map, [], seed=999)

    def test_blind_rejects_a_junction_replacing_an_allocated_trial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            allocation = complete_run(run_dir)
            trial_id = allocation["trials"][0]["trial_id"]
            trial_dir = run_dir / "dispatch" / trial_id
            outside = root / "outside-trial"
            os.replace(trial_dir, outside)
            self.create_junction(trial_dir, outside)
            with self.assertRaisesRegex(ValueError, "link, junction, or reparse point"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )
            self.assertFalse((root / "public").exists())

    def test_blind_rejects_an_undeclared_empty_trial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            allocation = complete_run(run_dir)
            trial_id = allocation["trials"][0]["trial_id"]
            (run_dir / "dispatch" / trial_id / "undeclared-empty").mkdir()
            with self.assertRaisesRegex(ValueError, "directory set mismatch"):
                harness.make_blind_bundle(
                    run_dir,
                    root / "public",
                    root / "private" / "map.json",
                    seed=654,
                )
            self.assertFalse((root / "public").exists())

    def test_blind_rejects_undeclared_run_and_dispatch_root_entries(self) -> None:
        def add_run_file(run_dir: Path) -> None:
            (run_dir / "undeclared-run-file.txt").write_text("extra\n", encoding="utf-8")

        def add_dispatch_file(run_dir: Path) -> None:
            (run_dir / "dispatch" / "undeclared-root-file.txt").write_text("extra\n", encoding="utf-8")

        def add_dispatch_directory(run_dir: Path) -> None:
            (run_dir / "dispatch" / "undeclared-direct-child").mkdir()

        for label, mutate in (
            ("run-file", add_run_file),
            ("dispatch-file", add_dispatch_file),
            ("dispatch-directory", add_dispatch_directory),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                complete_run(run_dir)
                mutate(run_dir)
                with self.assertRaisesRegex(ValueError, "set mismatch"):
                    harness.make_blind_bundle(
                        run_dir,
                        root / "public",
                        root / "private" / "map.json",
                        seed=654,
                    )
                self.assertFalse((root / "public").exists())

    def test_blind_rejects_a_junction_public_output_without_writing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            complete_run(run_dir)
            public_target = root / "outside-public"
            public_target.mkdir()
            public_link = root / "public-link"
            self.create_junction(public_link, public_target)
            private_map = root / "private" / "map.json"
            with self.assertRaisesRegex(ValueError, "link, junction, or reparse point"):
                harness.make_blind_bundle(run_dir, public_link, private_map, seed=654)
            self.assertEqual([], list(public_target.iterdir()))
            self.assertFalse(private_map.exists())

    def test_scoring_and_adjudication_reject_a_junction_packet_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            packet_target = root / "outside-packets"
            os.replace(public_dir / "packets", packet_target)
            self.create_junction(public_dir / "packets", packet_target)
            with self.assertRaisesRegex(ValueError, "link, junction, or reparse point"):
                harness.make_adjudication_plan(public_dir, [])
            with self.assertRaisesRegex(ValueError, "link, junction, or reparse point"):
                harness.aggregate_scores(public_dir, private_map, [], seed=999)

    def test_scoring_and_adjudication_reject_an_extra_empty_public_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            extra_directory = public_dir / "undeclared-empty"
            extra_directory.mkdir()
            for path in (extra_directory, public_dir):
                os.utime(
                    path,
                    ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
                )
            with self.assertRaisesRegex(ValueError, "directory set mismatch"):
                harness.make_adjudication_plan(public_dir, [])
            with self.assertRaisesRegex(ValueError, "directory set mismatch"):
                harness.aggregate_scores(public_dir, private_map, [], seed=999)

    def test_scoring_and_adjudication_reject_extra_public_files(self) -> None:
        for relative_path in ("CONDITION-MAP.json", "packets/condition-map.json"):
            with self.subTest(relative_path=relative_path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                public_dir = root / "public"
                private_map = root / "private" / "map.json"
                complete_run(run_dir)
                harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
                extra_file = public_dir / relative_path
                extra_file.write_text('{"leak":"skill"}\n', encoding="utf-8")
                for path in (extra_file, extra_file.parent):
                    os.utime(
                        path,
                        ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
                    )
                with self.assertRaisesRegex(ValueError, "file set mismatch"):
                    harness.make_adjudication_plan(public_dir, [])
                with self.assertRaisesRegex(ValueError, "public scoring bundle"):
                    harness.aggregate_scores(public_dir, private_map, [], seed=999)

    def test_adjudication_rejects_add_plan_remove_condition_map_attack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            first = root / "scorer-a.jsonl"
            second = root / "scorer-b.jsonl"
            write_score_file(public_dir, first, "scorer-a")
            write_score_file(public_dir, second, "scorer-b")

            mapping = json.loads(private_map.read_text(encoding="utf-8"))
            leak_path = public_dir / "CONDITION-MAP.json"
            harness.write_json(
                leak_path,
                {row["blind_id"]: row["condition"] for row in mapping["trials"]},
            )
            for path in (leak_path, public_dir):
                os.utime(
                    path,
                    ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
                )
            with self.assertRaisesRegex(ValueError, "file set mismatch"):
                harness.make_adjudication_plan(public_dir, [first, second])

            leak_path.unlink()
            os.utime(
                public_dir,
                ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
            )
            plan = harness.make_adjudication_plan(public_dir, [first, second])
            self.assertNotIn("CONDITION-MAP.json", plan["public_bundle_file_hashes"])
            self.assertEqual(mapping["public_bundle_file_hashes"], plan["public_bundle_file_hashes"])
            self.assertNotIn("condition", json.dumps(plan, sort_keys=True))
            report = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            self.assertEqual([], report["validation_errors"])

    def test_adjudication_rejects_packet_swap_after_snapshot_before_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_id = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"][0]
            packet_path = public_dir / "packets" / f"{blind_id}.json"
            original_snapshot = harness.tree_snapshot
            swapped = [False]

            def snapshot_then_swap(path):
                snapshot = original_snapshot(path)
                if Path(path).resolve() == public_dir.resolve() and not swapped[0]:
                    swapped[0] = True
                    packet_path.write_bytes(packet_path.read_bytes() + b" ")
                    os.utime(
                        packet_path,
                        ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
                    )
                return snapshot

            with mock.patch.object(harness, "tree_snapshot", side_effect=snapshot_then_swap):
                with self.assertRaisesRegex(ValueError, "packet bytes do not match the captured public-bundle snapshot"):
                    harness.load_adjudication_bundle(public_dir, "adjudication test")
            self.assertTrue(swapped[0])

    def test_score_rejects_bundle_or_packet_swap_after_snapshot_before_parse(self) -> None:
        for target_kind in ("bundle", "packet"):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run_dir = root / "run"
                public_dir = root / "public"
                private_map = root / "private" / "map.json"
                complete_run(run_dir)
                harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
                bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
                target_path = (
                    public_dir / "bundle.json"
                    if target_kind == "bundle"
                    else public_dir / "packets" / f"{bundle['blind_order'][0]}.json"
                )
                original_snapshot = harness.tree_snapshot
                swapped = [False]

                def snapshot_then_swap(path):
                    snapshot = original_snapshot(path)
                    if Path(path).resolve() == public_dir.resolve() and not swapped[0]:
                        swapped[0] = True
                        target_path.write_bytes(target_path.read_bytes() + b" ")
                        os.utime(
                            target_path,
                            ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
                        )
                    return snapshot

                expected = (
                    "bundle index bytes do not match the captured public-bundle snapshot"
                    if target_kind == "bundle"
                    else "packet bytes do not match the captured public-bundle snapshot"
                )
                with mock.patch.object(harness, "tree_snapshot", side_effect=snapshot_then_swap):
                    with self.assertRaisesRegex(ValueError, expected):
                        harness.aggregate_scores(public_dir, private_map, [], seed=999)
                self.assertTrue(swapped[0])

    def test_scoring_and_adjudication_reject_public_metadata_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            blind_id = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))["blind_order"][0]
            packet_path = public_dir / "packets" / f"{blind_id}.json"
            changed = harness.PUBLIC_BUNDLE_MTIME_NS + 1_000_000_000
            os.utime(packet_path, ns=(changed, changed))
            with self.assertRaisesRegex(ValueError, "modification metadata"):
                harness.make_adjudication_plan(public_dir, [])
            with self.assertRaisesRegex(ValueError, "modification metadata"):
                harness.aggregate_scores(public_dir, private_map, [], seed=999)

    def test_adjudication_rejects_untrusted_blind_id_before_packet_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            bundle_path = public_dir / "bundle.json"
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            bundle["blind_order"][0] = "../../outside"
            harness.write_json(bundle_path, bundle)
            os.utime(
                bundle_path,
                ns=(harness.PUBLIC_BUNDLE_MTIME_NS, harness.PUBLIC_BUNDLE_MTIME_NS),
            )
            with self.assertRaisesRegex(ValueError, "blind order is malformed"):
                harness.make_adjudication_plan(public_dir, [])

    def test_plan_and_report_outputs_cannot_mutate_frozen_input_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            public_dir = root / "public"
            private_map = root / "private" / "map.json"
            complete_run(run_dir)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)

            with contextlib.redirect_stderr(io.StringIO()):
                plan_exit = harness.main(
                    [
                        "adjudication-plan",
                        "--public-bundle",
                        str(public_dir),
                        "--initial-scores",
                        str(root / "unused-a.jsonl"),
                        "--out",
                        str(public_dir / "plan.json"),
                    ]
                )
            self.assertEqual(2, plan_exit)
            self.assertFalse((public_dir / "plan.json").exists())

            with contextlib.redirect_stderr(io.StringIO()):
                plan_run_exit = harness.main(
                    [
                        "adjudication-plan",
                        "--public-bundle",
                        str(public_dir),
                        "--initial-scores",
                        str(root / "unused-a.jsonl"),
                        "--out",
                        str(run_dir / "trial-output" / "plan.json"),
                    ]
                )
            self.assertEqual(2, plan_run_exit)
            self.assertFalse((run_dir / "trial-output").exists())

            with contextlib.redirect_stderr(io.StringIO()):
                score_exit = harness.main(
                    [
                        "score",
                        "--public-bundle",
                        str(public_dir),
                        "--private-map",
                        str(private_map),
                        "--initial-scores",
                        str(root / "unused-a.jsonl"),
                        "--out",
                        str(run_dir / "report.json"),
                    ]
                )
            self.assertEqual(2, score_exit)
            self.assertFalse((run_dir / "report.json").exists())

    def test_score_command_refuses_to_overwrite_existing_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "report.json"
            out.write_text("sentinel\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                result = harness.main(
                    [
                        "score",
                        "--public-bundle",
                        str(root / "unused-public"),
                        "--private-map",
                        str(root / "unused-map.json"),
                        "--initial-scores",
                        str(root / "unused-scores.jsonl"),
                        "--out",
                        str(out),
                    ]
                )
            self.assertEqual(2, result)
            self.assertEqual("sentinel\n", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
