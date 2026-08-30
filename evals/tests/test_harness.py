from __future__ import annotations

import importlib.util
import base64
import copy
import contextlib
import hashlib
import io
import json
import os
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
    allocation = json.loads((run_dir / "allocation.private.json").read_text(encoding="utf-8"))
    cases = harness.case_index()
    oracles = harness.oracle_index()
    operator_path = HARNESS_PATH.parent / "run_trials.py"
    harness.write_json(
        run_dir / "operator-config.json",
        {
            "schema_version": "1.0",
            "operator_version": "test",
            "operator_script": str(operator_path),
            "operator_script_sha256": harness.sha256_file(operator_path),
            "created_at": "2026-08-30T00:00:00Z",
            "repository": {"head": "test-commit", "status_short": []},
            "allocation_seed": allocation["seed"],
            "replicates": allocation["replicates"],
            "fixture_hashes": allocation["fixture_hashes"],
            "skill_resource_hashes": allocation["skill_resource_hashes"],
            "dispatch_order": allocation["dispatch_order"],
            "expected_commit": "test-commit",
            "codex": {
                "requested_command": "synthetic-codex",
                "resolved_path": "synthetic-codex",
                "binary_sha256": "0" * 64,
                "version_output": "synthetic-codex 1.0",
            },
            "model_catalog_entry": {"slug": "test-model"},
            "model_catalog_raw_sha256": "1" * 64,
            "model": "test-model",
            "reasoning_effort": "test",
            "model_verbosity": "low",
            "temperature": "unset",
            "top_p": "unset",
            "max_output_tokens": "unset",
            "request_seed": "unsupported",
            "sandbox": "read-only",
            "network_search": False,
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "skip_host_skill_discovery": True,
            "disabled_features": ["synthetic-disabled-feature"],
            "jobs": 1,
            "timeout_seconds": 30,
            "python": "synthetic-python",
            "platform": "synthetic-platform",
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
        started = {
            "schema_version": "1.0",
            "operator_version": "test",
            "trial_id": trial["trial_id"],
            "case_id": trial["case_id"],
            "pair_id": trial["pair_id"],
            "replicate": trial["replicate"],
            "condition": trial["condition"],
            "allocated_model_seed": trial["model_seed"],
            "model_seed_applied": False,
            "model_seed_note": "synthetic test operator",
            "started_at": "2026-08-30T00:00:00Z",
            "prompt_sha256": harness.sha256_file(prompt_path),
            "allowed_file_hashes": trial["trial_file_hashes"],
            "argv": ["synthetic-codex", "exec"],
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
) -> None:
    bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    for blind_id in bundle["blind_order"]:
        if blind_ids is not None and blind_id not in blind_ids:
            continue
        packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
        ratings = {
            dimension: (
                (rating_overrides or {}).get((blind_id, dimension), rating)
                if dimension in packet["applicable_rubric_dimensions"]
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
        self.assertEqual(8, result["case_count"])
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
            self.assertEqual(16, result["trial_count"])
            allocation = json.loads((run_dir / "allocation.private.json").read_text(encoding="utf-8"))
            by_pair: dict[str, list[dict]] = {}
            for trial in allocation["trials"]:
                by_pair.setdefault(trial["pair_id"], []).append(trial)
            self.assertEqual(8, len(by_pair))
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
            (lambda item: item.__setitem__("blind_id", "blind-NOTHEX"), "invalid format"),
            (lambda item: item.__setitem__("case_id", "CASE-01"), "invalid format"),
            (lambda item: item["ratings"].__setitem__("decision_quality", 5), "decision_quality"),
            (
                lambda item: item.__setitem__("critical_failures", ["UNSUPPORTED_WTP", "UNSUPPORTED_WTP"]),
                "invalid or duplicate",
            ),
            (lambda item: item.__setitem__("rationale", "x" * 2001), "1 to 2000"),
        ]
        for mutate, expected_error in mutations:
            candidate = copy.deepcopy(record)
            mutate(candidate)
            errors = harness.validate_score_record(candidate, packet)
            self.assertTrue(any(expected_error in error for error in errors), errors)

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
            self.assertEqual(16, len(report["trial_results"]))
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
            write_score_file(
                public_dir,
                adjudicator,
                "scorer-3",
                critical_overrides={blind_id: [codes[1]]},
                blind_ids={blind_id},
            )
            report = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
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
            self.assertEqual("test-model", report["manifest"]["configuration"]["operator"]["model"])
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
            self.assertEqual(1, plan["target_count"])
            self.assertEqual(blind_id, plan["targets"][0]["blind_id"])
            self.assertEqual([disputed], plan["targets"][0]["disputed_dimensions"])
            self.assertEqual(
                [("initial", "scorer-a"), ("initial", "scorer-b")],
                [
                    (item["role"], item["scorer_id"])
                    for item in plan["initial_scorer_files"]
                ],
            )
            self.assertTrue(all(item["sha256"] for item in plan["initial_scorer_files"]))
            self.assertNotIn("condition", json.dumps(plan, sort_keys=True))

            missing = harness.aggregate_scores(public_dir, private_map, [first, second], seed=999)
            self.assertTrue(any("expected exactly one targeted adjudicator" in error for error in missing["validation_errors"]))

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
            )
            self.assertTrue(any("unplanned record" in error for error in unplanned["validation_errors"]))
            self.assertTrue(
                any("expected exactly one targeted adjudicator" in error for error in unplanned["validation_errors"])
            )

            write_score_file(public_dir, adjudicator, "scorer-c", rating=0, blind_ids={blind_id})
            report = harness.aggregate_scores(
                public_dir,
                private_map,
                [first, second],
                seed=999,
                adjudicator_score_paths=[adjudicator],
            )
            self.assertEqual([], report["validation_errors"])
            row = next(item for item in report["trial_results"] if item["blind_id"] == blind_id)
            self.assertEqual(2.0, row["dimension_scores"][disputed])
            self.assertEqual(3.5, row["dimension_scores"][undisputed])
            self.assertEqual(
                ["initial", "initial", "adjudicator"],
                [item["role"] for item in report["manifest"]["hashes"]["scorer_files"]],
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
