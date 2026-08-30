from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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


class HarnessTests(unittest.TestCase):
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

    def test_parser_accepts_plain_json_and_one_enclosing_fence(self) -> None:
        value = {"a": 1}
        plain, errors = harness.parse_model_output(json.dumps(value))
        self.assertEqual(value, plain)
        self.assertEqual([], errors)
        fenced, errors = harness.parse_model_output("```json\n{\"a\": 1}\n```")
        self.assertEqual(value, fenced)
        self.assertEqual([], errors)

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
            harness.prepare_trials(run_dir, replicates=1, seed=321)
            allocation = json.loads((run_dir / "allocation.private.json").read_text(encoding="utf-8"))
            cases = harness.case_index()
            oracles = harness.oracle_index()
            for trial in allocation["trials"]:
                response = perfect_response(cases[trial["case_id"]], oracles[trial["case_id"]])
                harness.write_json(run_dir / "dispatch" / trial["trial_id"] / "response.json", response)
            harness.make_blind_bundle(run_dir, public_dir, private_map, seed=654)
            bundle = json.loads((public_dir / "bundle.json").read_text(encoding="utf-8"))
            score_paths = []
            for scorer_id in ("scorer-a", "scorer-b"):
                lines = []
                for blind_id in bundle["blind_order"]:
                    packet = json.loads((public_dir / "packets" / f"{blind_id}.json").read_text(encoding="utf-8"))
                    serialized_packet = json.dumps(packet)
                    self.assertNotIn('"condition"', serialized_packet)
                    self.assertNotIn('"baseline"', serialized_packet)
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


if __name__ == "__main__":
    unittest.main()
