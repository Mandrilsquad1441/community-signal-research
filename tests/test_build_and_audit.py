from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import (
    artifact_bytes,
    csr,
    issue_codes,
    make_bundle,
    run_cli,
    sha256_prefixed,
    signal,
    source,
    write_bundle,
)


class BuildAndAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()
        write_bundle(self.study, self.bundle)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_valid_build_is_warning_free_and_strict_audit_passes(self) -> None:
        report, exit_code = csr.build_artifacts(self.study)
        self.assertEqual(0, exit_code)
        self.assertEqual("pass", report["status"])
        self.assertEqual(0, report["counts"]["errors"])
        self.assertEqual(0, report["counts"]["warnings"])
        self.assertEqual(100.0, report["coverage_execution_score"])

        fresh_report, context = csr.analyze(self.study)
        self.assertEqual([], csr.audit_artifacts(self.study, fresh_report, context))
        completed = run_cli("audit", str(self.study), "--strict")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_build_is_byte_deterministic(self) -> None:
        first_report, first_code = csr.build_artifacts(self.study)
        first = artifact_bytes(self.study)
        second_report, second_code = csr.build_artifacts(self.study)
        second = artifact_bytes(self.study)
        self.assertEqual((0, 0), (first_code, second_code))
        self.assertEqual(first, second)
        self.assertEqual(first_report["input_fingerprint"], second_report["input_fingerprint"])

    def test_semantic_fingerprint_ignores_jsonl_record_order(self) -> None:
        first_report, first_code = csr.build_artifacts(self.study)
        self.assertEqual(0, first_code)
        first = artifact_bytes(self.study)

        self.bundle["queries"].reverse()
        self.bundle["sources"].reverse()
        write_bundle(self.study, self.bundle)
        second_report, second_code = csr.build_artifacts(self.study)
        self.assertEqual(0, second_code)
        self.assertEqual(first_report["input_fingerprint"], second_report["input_fingerprint"])
        self.assertEqual(first, artifact_bytes(self.study))

    def test_audit_json_hashes_commit_exact_csv_and_markdown_bytes(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code)
        files = artifact_bytes(self.study)
        committed = json.loads(files["audit.json"])
        self.assertEqual(sha256_prefixed(files["signals.csv"]), committed["artifact_hashes"]["signals.csv"])
        self.assertEqual(sha256_prefixed(files["findings.md"]), committed["artifact_hashes"]["findings.md"])
        self.assertEqual(report["artifact_hashes"], committed["artifact_hashes"])

    def test_tampered_artifact_is_rejected(self) -> None:
        csr.build_artifacts(self.study)
        path = self.study / "artifacts" / "signals.csv"
        path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        result = run_cli("audit", str(self.study), "--json")
        self.assertEqual(1, result.returncode)
        parsed = json.loads(result.stdout)
        self.assertIn("MODIFIED_ARTIFACT", issue_codes(parsed, "error"))

    def test_stale_artifacts_after_valid_input_change_are_rejected(self) -> None:
        csr.build_artifacts(self.study)
        signal(self.bundle)["hypothesis"] = "A changed but still valid hypothesis."
        write_bundle(self.study, self.bundle)
        report, context = csr.analyze(self.study)
        issues = csr.audit_artifacts(self.study, report, context)
        self.assertTrue(issues)
        self.assertEqual({"MODIFIED_ARTIFACT"}, {item.code for item in issues})

    def test_extra_and_missing_artifacts_are_rejected(self) -> None:
        csr.build_artifacts(self.study)
        (self.study / "artifacts" / "unexpected.txt").write_text("extra", encoding="utf-8")
        (self.study / "artifacts" / "findings.md").unlink()
        report, context = csr.analyze(self.study)
        codes = {item.code for item in csr.audit_artifacts(self.study, report, context)}
        self.assertEqual({"EXTRA_ARTIFACT", "MISSING_ARTIFACT"}, codes)

    def test_missing_artifact_directory_is_rejected(self) -> None:
        report, context = csr.analyze(self.study)
        issues = csr.audit_artifacts(self.study, report, context)
        self.assertEqual(["MISSING_ARTIFACT"], [item.code for item in issues])

    def test_failed_validation_preserves_last_committed_artifacts(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code)
        prior = artifact_bytes(self.study)
        source(self.bundle, "src-001")["excerpt"] = "words absent from the captured source unit"
        write_bundle(self.study, self.bundle)

        failed_report, failed_code = csr.build_artifacts(self.study)
        self.assertEqual(1, failed_code)
        self.assertIn("QUOTE_MISMATCH", issue_codes(failed_report, "error"))
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_failed_malformed_json_build_preserves_last_committed_artifacts(self) -> None:
        csr.build_artifacts(self.study)
        prior = artifact_bytes(self.study)
        (self.study / "signal-catalog.json").write_text("{not json", encoding="utf-8")
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(1, code)
        self.assertIn("JSON", issue_codes(report, "error"))
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_write_failure_preserves_entire_prior_artifact_set(self) -> None:
        """The three-file artifact commit must be transactional, not merely per-file atomic."""
        csr.build_artifacts(self.study)
        prior = artifact_bytes(self.study)
        signal(self.bundle)["hypothesis"] = "A valid changed hypothesis for a new build."
        write_bundle(self.study, self.bundle)
        original_write = csr.atomic_write_text

        def fail_on_findings(path: Path, content: str) -> None:
            if path.name == "findings.md":
                raise OSError("simulated disk failure")
            original_write(path, content)

        with mock.patch.object(csr, "atomic_write_text", side_effect=fail_on_findings):
            with self.assertRaises(OSError):
                csr.build_artifacts(self.study)
        self.assertEqual(prior, artifact_bytes(self.study))


class MalformedAndLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()
        write_bundle(self.study, self.bundle)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_duplicate_json_object_key_is_rejected(self) -> None:
        raw = (self.study / "study-plan.json").read_text(encoding="utf-8")
        duplicate = raw.replace('"study_id": "adversarial-study",', '"study_id": "first",\n  "study_id": "second",')
        (self.study / "study-plan.json").write_text(duplicate, encoding="utf-8")
        report, _ = csr.analyze(self.study)
        self.assertIn("JSON", issue_codes(report, "error"))

    def test_nonfinite_json_number_is_rejected(self) -> None:
        path = self.study / "study-plan.json"
        raw = path.read_text(encoding="utf-8").replace('"recency_days": 365', '"recency_days": NaN')
        path.write_text(raw, encoding="utf-8")
        report, _ = csr.analyze(self.study)
        self.assertIn("JSON", issue_codes(report, "error"))

    def test_malformed_jsonl_line_is_rejected_with_line_location(self) -> None:
        path = self.study / "query-log.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
        report, _ = csr.analyze(self.study)
        matching = [item for item in report["issues"] if item["code"] == "JSONL"]
        self.assertEqual(1, len(matching))
        self.assertIn(":3", matching[0]["path"])

    def test_duplicate_key_inside_jsonl_is_rejected(self) -> None:
        path = self.study / "query-log.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"id": "qry-support",', '"id": "qry-support", "id": "qry-other",')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report, _ = csr.analyze(self.study)
        self.assertIn("JSONL", issue_codes(report, "error"))

    def test_invalid_utf8_input_is_rejected(self) -> None:
        (self.study / "research-notes.json").write_bytes(b"\xff\xfe\x00")
        report, _ = csr.analyze(self.study)
        self.assertIn("ENCODING", issue_codes(report, "error"))

    def test_missing_required_input_is_rejected(self) -> None:
        (self.study / "research-notes.json").unlink()
        report, _ = csr.analyze(self.study)
        self.assertIn("MISSING_FILE", issue_codes(report, "error"))

    def test_file_byte_limit_is_enforced(self) -> None:
        with mock.patch.object(csr, "MAX_FILE_BYTES", 100):
            report, _ = csr.analyze(self.study)
        self.assertIn("FILE_TOO_LARGE", issue_codes(report, "error"))

    def test_jsonl_record_limit_is_enforced(self) -> None:
        with mock.patch.object(csr, "MAX_RECORDS", 2):
            report, _ = csr.analyze(self.study)
        self.assertIn("TOO_MANY_RECORDS", issue_codes(report, "error"))

    def test_captured_text_limit_is_enforced(self) -> None:
        source(self.bundle, "src-001")["captured_text"] = "x" * 101
        source(self.bundle, "src-001")["excerpt"] = "x"
        write_bundle(self.study, self.bundle)
        with mock.patch.object(csr, "MAX_CAPTURED_TEXT", 100):
            report, _ = csr.analyze(self.study)
        self.assertIn("STRING_TOO_LARGE", issue_codes(report, "error"))

    def test_general_string_limit_is_enforced(self) -> None:
        self.bundle["plan"]["question"] = "q" * 101
        write_bundle(self.study, self.bundle)
        with mock.patch.object(csr, "MAX_GENERAL_STRING", 100):
            report, _ = csr.analyze(self.study)
        self.assertIn("STRING_TOO_LARGE", issue_codes(report, "error"))

    def test_validate_strict_fails_on_warning_but_non_strict_passes(self) -> None:
        self.bundle["plan"]["counterevidence_status"] = "partial"
        signal(self.bundle)["claimed_level"] = "recurring"
        write_bundle(self.study, self.bundle)
        non_strict = run_cli("validate", str(self.study))
        strict = run_cli("validate", str(self.study), "--strict")
        self.assertEqual(0, non_strict.returncode, non_strict.stdout + non_strict.stderr)
        self.assertEqual(1, strict.returncode, strict.stdout + strict.stderr)


if __name__ == "__main__":
    unittest.main()
