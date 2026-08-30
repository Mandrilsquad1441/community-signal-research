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

    def test_crlf_only_artifact_mutation_is_rejected_as_byte_mismatch(self) -> None:
        csr.build_artifacts(self.study)
        path = self.study / "artifacts" / "findings.md"
        original = path.read_bytes()
        self.assertIn(b"\n", original)
        path.write_bytes(original.replace(b"\n", b"\r\n"))
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

    def test_json_numeric_tokens_are_bounded_and_finite(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer exceeds"):
            csr.strict_json_loads("9" * (csr.MAX_JSON_NUMBER_CHARS + 1))
        with self.assertRaisesRegex(ValueError, "non-finite JSON number"):
            csr.strict_json_loads("1e9999")

    def test_lone_surrogate_json_key_is_rejected_and_json_cli_remains_safe(self) -> None:
        path = self.study / "study-plan.json"
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace("{", '{"\\ud800":1,', 1), encoding="utf-8")
        report, _ = csr.analyze(self.study)
        self.assertIn("JSON", issue_codes(report, "error"))
        completed = run_cli("validate", str(self.study), "--json")
        self.assertEqual(1, completed.returncode, completed.stderr)
        parsed = json.loads(completed.stdout)
        self.assertIn("JSON", issue_codes(parsed, "error"))

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

    def test_input_growth_during_read_is_bounded(self) -> None:
        path = self.study / "bounded.json"
        path.write_bytes(b"{}")
        audit = csr.Audit()
        real_read = csr.os.read
        grew = False

        def grow_then_read(fd: int, count: int) -> bytes:
            nonlocal grew
            if not grew:
                grew = True
                with path.open("ab") as stream:
                    stream.write(b"x" * 200)
            return real_read(fd, count)

        with (
            mock.patch.object(csr, "MAX_FILE_BYTES", 100),
            mock.patch.object(csr.os, "read", side_effect=grow_then_read),
        ):
            self.assertIsNone(csr.read_input_text(path, audit))
        self.assertIn("FILE_TOO_LARGE", {issue.code for issue in audit.errors})

    def test_input_descriptor_must_match_the_validated_path(self) -> None:
        path = self.study / "inside.json"
        outside = self.study.parent / "outside.json"
        path.write_bytes(b"{}")
        outside.write_bytes(b'{"outside":true}')
        audit = csr.Audit()
        real_open = csr.os.open

        def substitute_descriptor(open_path: object, flags: int, *args: object) -> int:
            if Path(open_path) == path:
                return real_open(outside, flags, *args)
            return real_open(open_path, flags, *args)

        with mock.patch.object(csr.os, "open", side_effect=substitute_descriptor):
            self.assertIsNone(csr.read_input_text(path, audit))
        self.assertIn("UNSAFE_INPUT_PATH", {issue.code for issue in audit.errors})

    def test_jsonl_record_limit_is_enforced(self) -> None:
        with mock.patch.object(csr, "MAX_RECORDS", 2):
            report, context = csr.analyze(self.study)
        self.assertIn("TOO_MANY_RECORDS", issue_codes(report, "error"))
        self.assertLessEqual(len(context["sources"]), 2)

    def test_jsonl_physical_lines_and_error_parsing_are_bounded(self) -> None:
        path = self.study / "query-log.jsonl"
        path.write_text("\n" * 5, encoding="utf-8")
        audit = csr.Audit()
        with (
            mock.patch.object(csr, "MAX_RECORDS", 2),
            mock.patch.object(csr, "MAX_JSONL_EXTRA_LINES", 2),
        ):
            records = csr.load_jsonl(path, audit)
        self.assertEqual([], records)
        self.assertIn("JSONL_LINE_LIMIT", {issue.code for issue in audit.errors})

        path.write_text("invalid\n" * 10, encoding="utf-8")
        audit = csr.Audit()
        with (
            mock.patch.object(csr, "MAX_ISSUES", 2),
            mock.patch.object(csr, "MAX_RECORDS", 100),
            mock.patch.object(csr, "MAX_JSONL_EXTRA_LINES", 100),
            mock.patch.object(csr, "strict_json_loads", side_effect=ValueError("bad")) as parser,
        ):
            csr.load_jsonl(path, audit)
        self.assertEqual(3, parser.call_count)
        self.assertTrue(audit.issue_limit_reached)
        self.assertEqual("ISSUE_LIMIT", audit.issues[-1].code)

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

    def test_rejected_oversized_strings_never_reach_semantic_parsers(self) -> None:
        payload = "oversized-private-material-" * 5
        self.assertGreater(len(payload), 100)
        item = source(self.bundle, "src-001")
        for key in ("platform", "community", "url", "thread_url"):
            item[key] = payload
        write_bundle(self.study, self.bundle)
        with (
            mock.patch.object(csr, "MAX_GENERAL_STRING", 100),
            mock.patch.object(csr, "canonicalize_url", wraps=csr.canonicalize_url) as canonicalize,
            mock.patch.object(csr, "normalized_label", wraps=csr.normalized_label) as normalize,
        ):
            report, _ = csr.analyze(self.study)
        self.assertIn("STRING_TOO_LARGE", issue_codes(report, "error"))
        self.assertNotIn(payload, [call.args[0] for call in canonicalize.call_args_list if call.args])
        self.assertNotIn(payload, [call.args[0] for call in normalize.call_args_list if call.args])

    def test_json_object_keys_obey_the_general_string_bound(self) -> None:
        payload = "private-unknown-key"
        with mock.patch.object(csr, "MAX_GENERAL_STRING", 8):
            with self.assertRaises(ValueError) as raised:
                csr.strict_json_loads(json.dumps({payload: 1}))
        self.assertIn("JSON object key exceeds 8 characters", str(raised.exception))
        self.assertNotIn(payload, str(raised.exception))

    def test_invalid_timestamp_payload_is_bounded_and_never_echoed(self) -> None:
        payload = "PRIVATE_TIMESTAMP_SECRET_" * 8
        source(self.bundle, "src-001")["published_at"] = payload
        write_bundle(self.study, self.bundle)
        report, _ = csr.analyze(self.study)
        self.assertIn("TIMESTAMP", issue_codes(report, "error"))
        self.assertNotIn(payload, json.dumps(report, sort_keys=True))

    def test_string_list_items_enforce_unicode_and_size_limits(self) -> None:
        self.bundle["plan"]["limitations"] = [chr(0xD800), "x" * 101]
        (self.study / "study-plan.json").write_text(
            json.dumps(self.bundle["plan"], indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(csr, "MAX_GENERAL_STRING", 100):
            report, _ = csr.analyze(self.study)
        self.assertIn("UNICODE", issue_codes(report, "error"))
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
