from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    analyze_bundle,
    artifact_bytes,
    csr,
    issue_codes,
    make_bundle,
    run_cli,
    set_reddit_thread,
    signal,
    source,
    write_bundle,
)


class CanonicalizationTests(unittest.TestCase):
    def test_reddit_alias_tracking_query_and_fragment_are_removed(self) -> None:
        raw = "HTTP://old.reddit.com/r/codex/comments/abc/title/?utm_source=x&sort=new#frag"
        self.assertEqual(
            "https://www.reddit.com/r/codex/comments/abc/title/",
            csr.canonicalize_url(raw),
        )

    def test_github_comment_fragment_is_preserved(self) -> None:
        raw = "https://github.com/org/repo/issues/7?utm_source=x#issuecomment-123"
        self.assertEqual(
            "https://github.com/org/repo/issues/7#issuecomment-123",
            csr.canonicalize_url(raw),
        )

    def test_hacker_news_keeps_only_numeric_item_id(self) -> None:
        raw = "http://www.news.ycombinator.com/item?foo=x&id=123&utm_source=y"
        self.assertEqual(
            "https://news.ycombinator.com/item?id=123",
            csr.canonicalize_url(raw),
        )

    def test_credentials_and_control_characters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://user:secret@example.com/path")
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://example.com/path\nnext")

    def test_generic_http_identity_and_ipv6_brackets_are_preserved(self) -> None:
        self.assertEqual(
            "http://forum.example/topic",
            csr.canonicalize_url("http://forum.example/topic"),
        )
        self.assertEqual(
            "https://[2001:db8::1]/resource",
            csr.canonicalize_url("https://[2001:DB8::1]/resource"),
        )


class RenderingSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_markdown_control_syntax_from_research_text_is_escaped(self) -> None:
        payload = "[click](https://evil.example) <script>alert(1)</script>"
        signal(self.bundle)["name"] = payload
        signal(self.bundle)["hypothesis"] = payload
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertEqual("pass", report["status"])
        rendered = csr.render_findings(report, context)
        self.assertNotIn("[click](https://evil.example)", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_csv_formula_prefix_after_whitespace_is_neutralized(self) -> None:
        signal(self.bundle)["name"] = " \t=HYPERLINK(\"https://evil.example\")"
        report, _ = analyze_bundle(self.study, self.bundle)
        rows = list(csv.DictReader(io.StringIO(csr.render_csv(report))))
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["name"].startswith("' \t="))

    def test_supplied_private_excerpt_is_withheld_from_findings(self) -> None:
        private = source(self.bundle, "src-001")
        private_text = "PRIVATE-CUSTOMER-TEXT-DO-NOT-RENDER"
        private.update(
            {
                "platform": "export",
                "community": "authorized-export",
                "source_type": "export_record",
                "url": None,
                "record_ref": "customer.csv:row-17",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "a" * 64,
                "thread_url": None,
                "unit_id": "export:row-17",
                "thread_id": "export:response-17",
                "author_key": "unknown",
                "title": "",
                "captured_text": private_text,
                "excerpt": private_text,
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("export")
        self.bundle["plan"]["scope"]["communities"].append("authorized-export")
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertEqual("pass", report["status"])
        rendered = csr.render_findings(report, context)
        self.assertNotIn(private_text, rendered)
        self.assertIn("Private excerpt withheld", rendered)
        self.assertIn("customer\\.csv:row\\-17", rendered)
        self.assertIn("sha256:" + "a" * 64, rendered)

    def test_promotional_citation_remains_visible_but_is_not_counted(self) -> None:
        source(self.bundle, "src-001")["promotional"] = "yes"
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertEqual("pass", report["status"])
        metric = report["signals"][0]
        self.assertNotIn("src-001", metric["support_source_ids"])
        self.assertIn("src-001", metric["ineligible_support_source_ids"])
        rendered = csr.render_findings(report, context)
        self.assertIn("Cited context excluded from positive counts", rendered)
        self.assertIn("src\\-001", rendered)
        self.assertIn("promotion: yes", rendered)

    def test_findings_disclose_scope_criteria_and_query_execution(self) -> None:
        report, context = analyze_bundle(self.study, self.bundle)
        rendered = csr.render_findings(report, context)
        self.assertIn("Date window:", rendered)
        self.assertIn("Communities in scope:", rendered)
        self.assertIn("Inclusion criteria:", rendered)
        self.assertIn("Exclusion criteria:", rendered)
        self.assertIn("### Query ledger", rendered)
        self.assertIn("research skill recurring failure workaround", rendered)
        self.assertIn("2026\\-08\\-30T09:00:00Z", rendered)

    def test_no_counter_source_wording_respects_not_searched_status(self) -> None:
        self.bundle["queries"] = [query for query in self.bundle["queries"] if query["id"] != "qry-counter"]
        self.bundle["plan"]["counterevidence_status"] = "planned"
        for item in self.bundle["sources"]:
            item["query_ids"] = [query_id for query_id in item["query_ids"] if query_id != "qry-counter"]
        self.bundle["sources"] = [item for item in self.bundle["sources"] if item["id"] != "src-900"]
        sig = signal(self.bundle)
        sig["counter_citations"] = []
        sig["claimed_level"] = "recurring"
        report, context = analyze_bundle(self.study, self.bundle)
        rendered = csr.render_findings(report, context)
        self.assertIn("No counter-oriented search is established", rendered)
        self.assertNotIn("No counterexample was found in the searched coverage", rendered)


class ProvenanceAndPrivacyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_raw_author_handle_is_rejected(self) -> None:
        source(self.bundle, "src-001")["author_key"] = "public_handle"
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("AUTHOR_PRIVACY", issue_codes(report, "error"))

    def test_public_source_cannot_smuggle_private_provenance(self) -> None:
        source(self.bundle, "src-001")["source_file_sha256"] = "sha256:" + "b" * 64
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("PUBLIC_FILE_HASH", issue_codes(report, "error"))

    def test_supplied_private_source_cannot_publish_a_url(self) -> None:
        item = source(self.bundle, "src-001")
        item["visibility"] = "supplied_private"
        item["record_ref"] = "export.jsonl:1"
        item["source_file_sha256"] = "sha256:" + "c" * 64
        item["source_type"] = "export_record"
        item["capture_method"] = "export"
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("PRIVATE_URL", issue_codes(report, "error"))

    def test_private_record_reference_rejects_personal_data(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "export",
                "community": "authorized-export",
                "source_type": "export_record",
                "url": None,
                "record_ref": "customer@example.com",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "d" * 64,
                "thread_url": None,
                "unit_id": "export:row-1",
                "thread_id": "export:row-1",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("export")
        self.bundle["plan"]["scope"]["communities"].append("authorized-export")
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertTrue({"RECORD_REF", "RECORD_REF_PII"} & issue_codes(report, "error"))

    def test_private_text_is_not_a_public_fingerprint_dictionary_oracle(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "export",
                "community": "authorized-export",
                "source_type": "export_record",
                "url": None,
                "record_ref": "survey.csv:row-1",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "e" * 64,
                "thread_url": None,
                "unit_id": "export:row-1",
                "thread_id": "export:row-1",
                "captured_text": "yes",
                "excerpt": "yes",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("export")
        self.bundle["plan"]["scope"]["communities"].append("authorized-export")
        first, _ = analyze_bundle(self.study, self.bundle)
        item["captured_text"] = "no"
        item["excerpt"] = "no"
        second, _ = analyze_bundle(self.study, self.bundle)
        self.assertEqual(first["input_fingerprint"], second["input_fingerprint"])
        item["source_file_sha256"] = "sha256:" + "f" * 64
        third, _ = analyze_bundle(self.study, self.bundle)
        self.assertNotEqual(second["input_fingerprint"], third["input_fingerprint"])

    def test_author_key_is_study_local_deterministic_and_normalized(self) -> None:
        result = run_cli(
            "init",
            str(self.study),
            "--question",
            "Which evidence recurs?",
            "--decision",
            "Choose a follow-up test.",
            "--mode",
            "quick",
            "--as-of",
            "2026-08-30",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        first = run_cli("author-key", "--study-dir", str(self.study), input_text="Alice\n")
        normalized = run_cli("author-key", "--study-dir", str(self.study), input_text="ＡＬＩＣＥ\n")
        other = run_cli("author-key", "--study-dir", str(self.study), input_text="Bob\n")
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, normalized.stdout)
        self.assertNotEqual(first.stdout, other.stdout)
        self.assertRegex(first.stdout.strip(), r"^author:[0-9a-f]{16}$")
        self.assertNotIn("alice", first.stdout.casefold())


class IntegrityHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def analyze(self) -> dict:
        report, _ = analyze_bundle(self.study, self.bundle)
        return report

    def test_reddit_thread_id_is_bound_to_native_url(self) -> None:
        source(self.bundle, "src-001")["thread_id"] = "reddit:t3_invented"
        self.assertIn("NATIVE_ID_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_platform_rejects_lookalike_host(self) -> None:
        item = source(self.bundle, "src-001")
        item["url"] = item["url"].replace("www.reddit.com", "evidence.invalid")
        self.assertIn("PLATFORM_URL_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_nonpublic_public_source_host_is_rejected(self) -> None:
        item = source(self.bundle, "src-001")
        item["platform"] = "forum"
        item["community"] = "internal"
        item["url"] = "https://127.0.0.1/post/1"
        item["thread_url"] = "https://127.0.0.1/thread/1"
        item["unit_id"] = "forum:post-1"
        item["thread_id"] = "forum:thread-1"
        self.bundle["plan"]["scope"]["platforms"].append("forum")
        self.bundle["plan"]["scope"]["communities"].append("internal")
        self.assertIn("NONPUBLIC_URL", issue_codes(self.analyze(), "error"))

    def test_community_case_cannot_manufacture_diversity(self) -> None:
        self.bundle["plan"]["scope"]["communities"] = ["r/alpha"]
        for item in self.bundle["sources"]:
            item["community"] = "r/Alpha" if item["community"].casefold() == "r/beta" else "r/alpha"
            item["url"] = item["url"].replace("/r/beta/", "/r/alpha/")
            item["thread_url"] = item["thread_url"].replace("/r/beta/", "/r/alpha/")
        signal(self.bundle)["claimed_level"] = "recurring"
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual(1, report["signals"][0]["communities"])
        self.assertEqual(1, report["coverage"]["actual"]["communities"])

    def test_query_cannot_include_sources_after_reporting_zero_results(self) -> None:
        support = self.bundle["queries"][0]
        support["results_seen"] = 0
        support["results_screened"] = 0
        support["pages_seen"] = 0
        self.assertIn("QUERY_COUNT_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_long_unbroken_excerpt_is_rejected(self) -> None:
        payload = "x" * (csr.MAX_EXCERPT_CHARS + 1)
        item = source(self.bundle, "src-001")
        item["captured_text"] = payload
        item["excerpt"] = payload
        self.assertIn("EXCERPT_TOO_LARGE", issue_codes(self.analyze(), "error"))

    def test_control_characters_and_control_keys_are_rejected(self) -> None:
        item = source(self.bundle, "src-001")
        item["captured_text"] = "before\x1bc\x00after"
        item["excerpt"] = item["captured_text"]
        report = self.analyze()
        self.assertIn("CONTROL_CHARACTER", issue_codes(report, "error"))
        with self.assertRaises(ValueError):
            csr.strict_json_loads('{"bad\\u001bkey":1}')

    def test_long_repost_chain_does_not_recurse(self) -> None:
        records = {
            f"src-{index}": {"repost_of": f"src-{index + 1}" if index < 1_999 else None}
            for index in range(2_000)
        }
        audit = csr.Audit()
        csr.detect_repost_cycles(records, audit)
        self.assertIn("REPOST_CHAIN", {issue.code for issue in audit.errors})

    def test_duplicate_origin_uses_utc_chronology(self) -> None:
        left = source(self.bundle, "src-001")
        right = source(self.bundle, "src-002")
        shared = " ".join(f"chronology-token-{index}" for index in range(20))
        left["captured_text"] = right["captured_text"] = shared
        left["published_at"] = "2026-08-30T09:00:00Z"
        right["published_at"] = "2026-08-30T10:00:00+02:00"
        by_id = {item["id"]: item for item in self.bundle["sources"]}
        audit = csr.Audit()
        roots, origins = csr.build_duplicate_groups(self.bundle["sources"], by_id, audit)
        self.assertEqual("src-002", origins[roots["src-001"]])

    def test_recent_share_uses_utc_dates(self) -> None:
        self.bundle["plan"]["recency_days"] = 1
        self.bundle["plan"]["date_window"] = {"start": "2026-08-28", "end": "2026-08-30"}
        for item in self.bundle["sources"]:
            item["published_at"] = "2026-08-29T12:00:00Z"
        source(self.bundle, "src-001")["published_at"] = "2026-08-28T23:30:00-02:00"
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual(1.0, report["signals"][0]["recent_share"])

    def test_fuzzy_scan_has_a_deterministic_pair_budget(self) -> None:
        sources = []
        roots = {}
        for index in range(449):
            source_id = f"src-{index}"
            sources.append(
                {
                    "id": source_id,
                    "captured_text": " ".join(f"token-{index}-{token}" for token in range(35)),
                    "duplicate_reviews": [],
                }
            )
            roots[source_id] = source_id
        audit = csr.Audit()
        csr.detect_fuzzy_duplicates(sources, roots, audit)
        self.assertIn("FUZZY_SCAN_SKIPPED", {issue.code for issue in audit.warnings})

    def test_malformed_list_and_huge_recency_return_json_not_tracebacks(self) -> None:
        self.bundle["queries"][0]["included_source_ids"] = [{}]
        write_bundle(self.study, self.bundle)
        malformed = run_cli("validate", str(self.study), "--json")
        self.assertEqual(1, malformed.returncode)
        json.loads(malformed.stdout)
        self.assertNotIn("Traceback", malformed.stderr)

        self.bundle = make_bundle()
        self.bundle["plan"]["recency_days"] = 1_000_000
        write_bundle(self.study, self.bundle)
        huge = run_cli("validate", str(self.study), "--json")
        self.assertEqual(1, huge.returncode)
        json.loads(huge.stdout)
        self.assertNotIn("Traceback", huge.stderr)

    def test_successful_build_json_stdout_is_one_json_document(self) -> None:
        write_bundle(self.study, self.bundle)
        built = run_cli("build", str(self.study), "--json")
        self.assertEqual(0, built.returncode, built.stderr)
        parsed = json.loads(built.stdout)
        self.assertEqual("pass", parsed["status"])


class FilesystemSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.study = self.root / "study"
        self.bundle = make_bundle()
        write_bundle(self.study, self.bundle)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_unexpected_artifact_entry_is_not_deleted_or_overwritten(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        unexpected = self.study / "artifacts" / "researcher-notes.txt"
        unexpected.write_text("keep me", encoding="utf-8")
        with self.assertRaises(ValueError):
            csr.build_artifacts(self.study)
        self.assertEqual("keep me", unexpected.read_text(encoding="utf-8"))
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_next_build_can_recover_a_hard_stop_between_directory_swaps(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        backup = self.study / ".csr-artifacts-backup-simulated"
        stage = self.study / ".csr-artifacts-stage-simulated"
        os.replace(self.study / "artifacts", backup)
        stage.mkdir()
        (stage / "signals.csv").write_text("partial new generation", encoding="utf-8")
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertEqual(prior, artifact_bytes(self.study))
        self.assertFalse(backup.exists())
        self.assertFalse(stage.exists())

    def test_symlinked_required_input_is_rejected_when_supported(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text((self.study / "study-plan.json").read_text(encoding="utf-8"), encoding="utf-8")
        (self.study / "study-plan.json").unlink()
        try:
            os.symlink(outside, self.study / "study-plan.json")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        report, _ = csr.analyze(self.study)
        self.assertIn("UNSAFE_INPUT_PATH", issue_codes(report, "error"))


if __name__ == "__main__":
    unittest.main()
