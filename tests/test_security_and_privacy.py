from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from tests.support import (
    analyze_bundle,
    artifact_bytes,
    csr,
    issue_codes,
    make_bundle,
    move_source_to_platform_query,
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

    def test_reddit_base36_post_and_comment_aliases_collapse(self) -> None:
        canonical_post = "https://www.reddit.com/r/codex/comments/1sqk7xj/title/"
        alias_post = "https://old.reddit.com/R/CoDeX/COMMENTS/01SQK7XJ/title/"
        self.assertEqual(canonical_post, csr.canonicalize_url(alias_post))
        canonical_comment = canonical_post + "abc123/"
        alias_comment = "https://www.reddit.com/r/codex/comments/01sqk7xj/title/00ABC123/"
        self.assertEqual(canonical_comment, csr.canonicalize_url(alias_comment))
        with self.assertRaisesRegex(ValueError, "positive base-36"):
            csr.canonicalize_url("https://www.reddit.com/r/codex/comments/000/title/")

    def test_github_comment_fragment_is_preserved(self) -> None:
        raw = "https://github.com/org/repo/issues/7?utm_source=x#issuecomment-12345678901"
        self.assertEqual(
            "https://github.com/org/repo/issues/7#issuecomment-12345678901",
            csr.canonicalize_url(raw),
        )
        for collection in ("issues", "discussions"):
            with self.subTest(collection=collection):
                self.assertEqual(
                    f"https://github.com/org/repo/{collection}/1",
                    csr.canonicalize_url(f"https://github.com/org/repo/{collection}/0001"),
                )
        self.assertEqual(
            "https://github.com/org/repo/issues/1",
            csr.canonicalize_url("https://github.com/org/repo/issues/1#issuecomment-\U00011f50"),
        )

    def test_hacker_news_keeps_only_numeric_item_id(self) -> None:
        raw = "http://www.news.ycombinator.com/item?foo=x&id=123&utm_source=y"
        self.assertEqual(
            "https://news.ycombinator.com/item?id=123",
            csr.canonicalize_url(raw),
        )
        self.assertEqual(
            "https://news.ycombinator.com/item?id=1",
            csr.canonicalize_url("https://news.ycombinator.com/item?id=0001"),
        )
        for invalid in ("0", "²", "１２"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                csr.canonicalize_url(f"https://news.ycombinator.com/item?id={invalid}")
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://news.ycombinator.com/item?id=1&id=2")

    def test_credentials_and_control_characters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://user:secret@example.com/path")
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://example.com/path\nnext")
        with self.assertRaisesRegex(ValueError, "port must be between"):
            csr.canonicalize_url("https://example.com:0/path")

    def test_generic_http_identity_and_ipv6_brackets_are_preserved(self) -> None:
        self.assertEqual(
            "http://forum.example/topic",
            csr.canonicalize_url("http://forum.example/topic"),
        )
        self.assertEqual(
            "https://[2001:db8::1]/resource",
            csr.canonicalize_url("https://[2001:DB8::1]/resource"),
        )

    def test_equivalent_ipv6_spellings_share_canonical_and_platform_identity(self) -> None:
        compressed = "https://[2001:4860:4860::8888]/resource"
        expanded = "https://[2001:4860:4860:0:0:0:0:8888]/resource"
        self.assertEqual(compressed, csr.canonicalize_url(expanded))
        left = {"visibility": "public", "url": compressed}
        right = {"visibility": "public", "url": expanded}
        self.assertEqual(csr.source_platform_identity(left), csr.source_platform_identity(right))
        with self.assertRaisesRegex(ValueError, "Bracketed URL hosts"):
            csr.canonicalize_url("https://[v1.example]/thread")
        with self.assertRaises(ValueError):
            csr.canonicalize_url("https://[192.0.2.1]/thread")

    def test_dot_segments_unreserved_escapes_and_trailing_host_dot_collapse(self) -> None:
        expected = "https://forum.example/thread/7"
        aliases = (
            "https://forum.example/thread/7",
            "https://forum.example/a/../thread/7",
            "https://forum.example/%74hread/7",
            "https://forum.example./thread/7",
        )
        self.assertEqual({expected}, {csr.canonicalize_url(value) for value in aliases})

    def test_raw_space_and_unicode_match_their_utf8_percent_encoded_forms(self) -> None:
        self.assertEqual(
            csr.canonicalize_url("https://forum.example/a b"),
            csr.canonicalize_url("https://forum.example/a%20b"),
        )
        self.assertEqual(
            csr.canonicalize_url("https://forum.example/café"),
            csr.canonicalize_url("https://forum.example/caf%C3%A9"),
        )

    def test_malformed_percent_and_sensitive_query_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed percent"):
            csr.canonicalize_url("https://forum.example/bad%2")
        for key in (
            "access_token",
            "id_token",
            "refresh_token",
            "session_id",
            "X-Amz-Signature",
            "X-Goog-Signature",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "credential or session secret"):
                csr.canonicalize_url(f"https://forum.example/thread?{key}=do-not-publish")
        with self.assertRaisesRegex(ValueError, "credential or session secret"):
            csr.canonicalize_url("https://forum.example/thread#access_token=do-not-publish")
        for separated in (
            "https://forum.example/thread?foo=1;token=do-not-publish",
            "https://forum.example/thread#foo=1;token=do-not-publish",
            "https://forum.example/thread;jsessionid=do-not-publish",
        ):
            with self.subTest(separated=separated), self.assertRaisesRegex(
                ValueError, "credential or session secret"
            ):
                csr.canonicalize_url(separated)
        for nested_key in ("token%5B%5D", "auth%5Btoken%5D", "auth%5Bauth%5D", "token.foo"):
            with self.subTest(nested_key=nested_key), self.assertRaisesRegex(
                ValueError, "credential or session secret"
            ):
                csr.canonicalize_url(f"https://forum.example/thread?{nested_key}=do-not-publish")
        for compound_key in (
            "x-api-key",
            "client%5Bapi_key%5D",
            "ASP.NET_SessionId",
            "JSESSIONID",
            "AWSAccessKeyId",
            "authToken",
            "sessionToken",
            "csrfToken",
            "userSession",
            "privateKey",
            "secretKey",
            "signingKey",
            "keyPairId",
        ):
            with self.subTest(compound_key=compound_key), self.assertRaisesRegex(
                ValueError, "credential or session secret"
            ):
                csr.canonicalize_url(f"https://forum.example/thread?{compound_key}=do-not-publish")
        with self.assertRaisesRegex(ValueError, "email address|personal data"):
            csr.canonicalize_url("https://forum.example/thread?contact=user%2540example.com")
        with self.assertRaisesRegex(ValueError, "credential or session secret"):
            csr.canonicalize_url("https://forum.example/thread?to%256ben=do-not-publish")
        with self.assertRaisesRegex(ValueError, "personal data"):
            csr.canonicalize_url("https://forum.example/thread?phone=41445551234")
        with self.assertRaisesRegex(ValueError, "forward slashes"):
            csr.canonicalize_url(r"https://forum.example\thread")

    def test_rejected_url_parameter_names_are_never_echoed(self) -> None:
        sentinel = "AKIA1234567890ABCDEF"
        url = f"https://forum.example/thread?secret%5B{sentinel}%5D=value"
        with self.assertRaises(ValueError) as raised:
            csr.canonicalize_url(url)
        self.assertNotIn(sentinel, str(raised.exception))
        completed = run_cli("canonicalize", url)
        self.assertEqual(2, completed.returncode)
        self.assertNotIn(sentinel, completed.stdout + completed.stderr)

    def test_generic_server_defined_path_and_query_distinctions_are_preserved(self) -> None:
        self.assertNotEqual(
            csr.canonicalize_url("https://forum.example/a//b"),
            csr.canonicalize_url("https://forum.example/a/b"),
        )
        self.assertNotEqual(
            csr.canonicalize_url("https://forum.example/resource/"),
            csr.canonicalize_url("https://forum.example/resource"),
        )
        self.assertNotEqual(
            csr.canonicalize_url("https://forum.example/?step=1&step=2"),
            csr.canonicalize_url("https://forum.example/?step=2&step=1"),
        )
        self.assertNotEqual(
            csr.canonicalize_url("https://gist.github.com/octocat/1#file-a-py"),
            csr.canonicalize_url("https://gist.github.com/octocat/1#file-b-py"),
        )

    def test_native_community_nondefault_ports_are_rejected(self) -> None:
        urls = (
            "https://github.com:8443/org/repo/issues/1",
            "https://www.reddit.com:8080/r/codex/comments/abc/title/",
            "https://news.ycombinator.com:444/item?id=1",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "nondefault port"):
                csr.canonicalize_url(url)

    def test_native_numeric_identifiers_and_ports_have_lexical_bounds(self) -> None:
        urls = (
            "https://www.reddit.com/r/codex/comments/" + "a" * (csr.MAX_REDDIT_ID_CHARS + 1) + "/title/",
            "https://github.com/org/repo/issues/" + "9" * (csr.MAX_NATIVE_DECIMAL_ID_CHARS + 1),
            "https://news.ycombinator.com/item?id=" + "9" * (csr.MAX_NATIVE_DECIMAL_ID_CHARS + 1),
            "https://forum.example:" + "9" * (csr.MAX_PORT_CHARS + 1) + "/thread",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                csr.canonicalize_url(url)

    def test_percent_encoded_hosts_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "host must not contain percent"):
            csr.canonicalize_url("https://%31%32%37.0.0.1/thread/1")

    def test_raw_unicode_host_requires_explicit_idna2008_a_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "ASCII IDNA A-label"):
            csr.canonicalize_url("https://faß.de/thread")
        self.assertEqual(
            "https://xn--fa-hia.de/thread",
            csr.canonicalize_url("https://xn--fa-hia.de/thread"),
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
        self.assertEqual("'\u180e=1", csr.safe_csv_cell("\u180e=1"))

    def test_only_validated_canonical_urls_are_rendered(self) -> None:
        item = source(self.bundle, "src-001")
        item["url"] = item["url"] + "?utm_source=private-campaign#ignored"
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertEqual("pass_with_warnings", report["status"])
        rendered = csr.render_findings(report, context)
        self.assertNotIn("utm_source", rendered)
        self.assertNotIn("private-campaign", rendered)
        validated_url = context["source_by_id"]["src-001"]["url"]
        self.assertEqual(csr.canonicalize_url(item["url"]), validated_url)

    def test_credential_bearing_public_url_is_a_hard_error(self) -> None:
        source(self.bundle, "src-001")["url"] += "?refresh_token=top-secret"
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("URL", issue_codes(report, "error"))

    def test_sensitive_dynamic_url_key_is_absent_from_json_audit(self) -> None:
        sentinel = "AKIA1234567890ABCDEF"
        source(self.bundle, "src-001")["url"] += f"?secret%5B{sentinel}%5D=value"
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("URL", issue_codes(report, "error"))
        self.assertNotIn(sentinel, json.dumps(report, sort_keys=True))

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
        move_source_to_platform_query(self.bundle, "src-001", "export")
        public_observation = source(self.bundle, "src-002")
        self.bundle["notes"]["observations"][0] = {
            "text": public_observation["excerpt"],
            "source_ids": ["src-002"],
        }
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

    def test_duplicate_groups_and_reasons_are_visible_in_findings(self) -> None:
        source(self.bundle, "src-002")["repost_of"] = "src-001"
        signal(self.bundle)["support_citations"] = list(signal(self.bundle)["support_citations"])
        signal(self.bundle)["support_citations"].remove("src-002")
        source(self.bundle, "src-002")["signal_ids"] = []
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertNotEqual("fail", report["status"], report["issues"])
        rendered = csr.render_findings(report, context)
        self.assertIn("## Duplicate and repost groups", rendered)
        self.assertIn(r"explicit\_repost", rendered)
        self.assertIn(r"src\-001", rendered)
        self.assertIn(r"src\-002", rendered)

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

    def test_supplied_private_source_requires_export_platform(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "source_type": "export_record",
                "url": None,
                "record_ref": "survey.jsonl:1",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "c" * 64,
                "thread_url": None,
                "unit_id": "export:1",
                "thread_id": "export:1",
            }
        )
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("PRIVATE_PLATFORM", issue_codes(report, "error"))

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

    def test_private_text_cannot_flow_into_public_signal_or_notes(self) -> None:
        private = source(self.bundle, "src-001")
        private.update(
            {
                "platform": "export",
                "community": "authorized-export",
                "source_type": "export_record",
                "url": None,
                "record_ref": "survey.csv:row-7",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "9" * 64,
                "thread_url": None,
                "unit_id": "export:row-7",
                "thread_id": "export:row-7",
                "author_key": "unknown",
                "captured_text": "Confidential roadmap phrase alpha beta gamma delta belongs only in the supplied file.",
                "excerpt": "Confidential roadmap phrase alpha beta gamma delta",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("export")
        self.bundle["plan"]["scope"]["communities"].append("authorized-export")
        public = source(self.bundle, "src-002")
        self.bundle["notes"]["observations"][0] = {"text": public["excerpt"], "source_ids": ["src-002"]}
        signal(self.bundle)["hypothesis"] = "The confidential roadmap phrase alpha beta gamma delta should be published."
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("PRIVATE_TEXT_IN_PUBLIC_OUTPUT", issue_codes(report, "error"))

    def test_private_identifier_cannot_flow_into_rendered_record_locator(self) -> None:
        private = source(self.bundle, "src-001")
        private.update(
            {
                "platform": "export",
                "community": "authorized-export",
                "source_type": "export_record",
                "url": None,
                "record_ref": "SecretClient42",
                "visibility": "supplied_private",
                "capture_method": "export",
                "source_file_sha256": "sha256:" + "8" * 64,
                "thread_url": None,
                "unit_id": "export:row-42",
                "thread_id": "export:row-42",
                "author_key": "unknown",
                "captured_text": "SecretClient42 reported an internal workflow constraint in the authorized export.",
                "excerpt": "SecretClient42 reported an internal workflow constraint",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("export")
        self.bundle["plan"]["scope"]["communities"].append("authorized-export")
        move_source_to_platform_query(self.bundle, "src-001", "export")
        public = source(self.bundle, "src-002")
        self.bundle["notes"]["observations"][0] = {"text": public["excerpt"], "source_ids": ["src-002"]}
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("PRIVATE_TEXT_IN_PUBLIC_OUTPUT", issue_codes(report, "error"))

    def test_private_identifier_components_cannot_flow_into_public_ids_or_urls(self) -> None:
        private = {
            "id": "src-private",
            "visibility": "supplied_private",
            "captured_text": "alpha beta secretidentifier gamma delta",
            "excerpt": "secretidentifier",
            "title": "",
            "notes": "",
            "record_ref": "opaque-row-7",
        }
        notes = {
            "observations": [],
            "inferences": [],
            "recommendation": {"text": "", "caveats": []},
            "next_tests": [],
            "coverage_notes": [],
            "stop_reason": "",
        }
        variants = (
            ([{"id": "src-public", "visibility": "public", "url": "https://forum.example/item", "thread_url": "https://forum.example/item", "title": "", "excerpt": "", "notes": ""}], [{"id": "sig-secretidentifier"}]),
            ([{"id": "src-secretidentifier", "visibility": "public", "url": "https://forum.example/item", "thread_url": "https://forum.example/item", "title": "", "excerpt": "", "notes": ""}], []),
            ([{**private, "record_ref": "records/secretidentifier"}], []),
            ([{"id": "src-public", "visibility": "public", "url": "https://forum.example/secretidentifier", "thread_url": "https://forum.example/secretidentifier", "title": "", "excerpt": "", "notes": ""}], []),
        )
        for public_sources, signals in variants:
            with self.subTest(public_sources=public_sources, signals=signals):
                audit = csr.Audit()
                sources = [private, *public_sources]
                csr.validate_public_output_privacy({}, [], sources, signals, notes, audit)
                self.assertIn("PRIVATE_TEXT_IN_PUBLIC_OUTPUT", {issue.code for issue in audit.errors})

    def test_public_output_fields_reject_email_and_phone_like_values(self) -> None:
        self.bundle["notes"]["recommendation"]["text"] = "Contact private.person@example.com before publishing."
        report, _ = analyze_bundle(self.study, self.bundle)
        self.assertIn("OUTPUT_PII", issue_codes(report, "error"))

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
        ignore_lines = (self.study / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".author-key", ignore_lines)
        self.assertIn(".csr-*.tmp", ignore_lines)
        self.assertIn(".csr-artifacts-stage-*/", ignore_lines)
        self.assertIn(".csr-artifacts-backup-*/", ignore_lines)
        first = run_cli("author-key", "--study-dir", str(self.study), "--platform", "reddit", input_text="Alice\n")
        normalized = run_cli("author-key", "--study-dir", str(self.study), "--platform", "reddit", input_text="ＡＬＩＣＥ\n")
        other = run_cli("author-key", "--study-dir", str(self.study), "--platform", "reddit", input_text="Bob\n")
        other_platform = run_cli("author-key", "--study-dir", str(self.study), "--platform", "github", input_text="Alice\n")
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, normalized.stdout)
        self.assertNotEqual(first.stdout, other.stdout)
        self.assertNotEqual(first.stdout, other_platform.stdout)
        self.assertRegex(first.stdout.strip(), r"^author:[0-9a-f]{16}$")
        self.assertNotIn("alice", first.stdout.casefold())

        at_limit = run_cli(
            "author-key",
            "--study-dir",
            str(self.study),
            "--platform",
            "reddit",
            input_text="a" * csr.MAX_AUTHOR_HANDLE_BYTES,
        )
        above_limit = run_cli(
            "author-key",
            "--study-dir",
            str(self.study),
            "--platform",
            "reddit",
            input_text="a" * (csr.MAX_AUTHOR_HANDLE_BYTES + 1),
        )
        self.assertEqual(0, at_limit.returncode, at_limit.stderr)
        self.assertNotEqual(0, above_limit.returncode)
        self.assertIn("exceeds 4096 UTF-8 bytes", above_limit.stderr)

        (self.study / ".author-key").write_bytes(b"a" * 100_000)
        malformed_key = run_cli(
            "author-key",
            "--study-dir",
            str(self.study),
            "--platform",
            "reddit",
            input_text="Alice\n",
        )
        self.assertEqual(2, malformed_key.returncode)
        self.assertIn(".author-key is malformed", malformed_key.stderr)

    def test_author_key_rejects_descriptor_substitution(self) -> None:
        initialized = run_cli(
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
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        secret_path = self.study / ".author-key"
        resolved_secret_path = secret_path.resolve(strict=True)
        outside_key = self.study.parent / "outside-author-key"
        outside_key.write_bytes(b"b" * 64)
        real_open = csr.os.open

        def substitute_descriptor(open_path: object, flags: int, *args: object) -> int:
            if Path(open_path).resolve(strict=True) == resolved_secret_path:
                return real_open(outside_key, flags, *args)
            return real_open(open_path, flags, *args)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(csr.os, "open", side_effect=substitute_descriptor),
            mock.patch.object(csr.sys, "stdin", io.StringIO("Alice\n")),
            mock.patch.object(csr.sys, "stdout", stdout),
            mock.patch.object(csr.sys, "stderr", stderr),
        ):
            exit_code = csr.main(
                [
                    "author-key",
                    "--study-dir",
                    str(self.study),
                    "--platform",
                    "reddit",
                ]
            )
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("safe private .author-key", stderr.getvalue())

    def test_init_refuses_oversized_existing_gitignore(self) -> None:
        self.study.mkdir(parents=True)
        ignore_path = self.study / ".gitignore"
        with ignore_path.open("wb") as stream:
            stream.truncate(csr.MAX_GITIGNORE_BYTES + 1)
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
        self.assertNotEqual(0, result.returncode)
        self.assertIn(".gitignore exceeds", result.stderr)
        self.assertEqual(csr.MAX_GITIGNORE_BYTES + 1, ignore_path.stat().st_size)
        self.assertFalse((self.study / "study-plan.json").exists())

    def test_init_bounds_gitignore_that_grows_during_read(self) -> None:
        self.study.mkdir(parents=True)
        ignore_path = self.study / ".gitignore"
        ignore_path.write_bytes(b"x\n")
        args = csr.build_parser().parse_args(
            [
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
            ]
        )
        real_read = csr.os.read
        grew = False

        def grow_then_read(fd: int, count: int) -> bytes:
            nonlocal grew
            if not grew:
                grew = True
                with ignore_path.open("ab") as stream:
                    stream.write(b"x" * 200)
            return real_read(fd, count)

        with (
            mock.patch.object(csr, "MAX_GITIGNORE_BYTES", 100),
            mock.patch.object(csr.os, "read", side_effect=grow_then_read),
            self.assertRaisesRegex(ValueError, r"\.gitignore exceeds"),
        ):
            args.func(args)
        self.assertFalse((self.study / "study-plan.json").exists())

    def test_init_final_ignore_block_overrides_existing_secret_negations(self) -> None:
        self.study.mkdir(parents=True)
        try:
            initialized_git = subprocess.run(
                ["git", "init", "--quiet"],
                cwd=self.study,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.skipTest("git executable is unavailable")
        if initialized_git.returncode != 0:
            self.skipTest(f"git init is unavailable: {initialized_git.stderr}")
        (self.study / ".gitignore").write_text(
            ".author-key\n!.author-key\n!**/.author-key\n",
            encoding="utf-8",
        )
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
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", ".author-key"],
            cwd=self.study,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, ignored.returncode, ignored.stderr)
        status = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=self.study,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertNotIn(".author-key", status.stdout)
        final_lines = (self.study / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            [".author-key", ".csr-build.lock", ".csr-*.tmp", ".csr-artifacts-stage-*/", ".csr-artifacts-backup-*/"],
            final_lines[-5:],
        )


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

    def test_reddit_thread_url_must_match_subreddit_and_post(self) -> None:
        source(self.bundle, "src-001")["thread_url"] = "https://www.reddit.com/r/other/comments/different/topic/"
        self.assertIn("THREAD_URL_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_reddit_comment_permalink_rejects_trailing_path_components(self) -> None:
        source(self.bundle, "src-001")["url"] += "arbitrary/extra/"
        self.assertIn("COMMENT_PERMALINK", issue_codes(self.analyze(), "error"))

    def test_github_issue_and_comment_identities_are_url_derived(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "github",
                "community": "Owner/Repo",
                "source_type": "comment",
                "url": "https://github.com/Owner/Repo/issues/7#issuecomment-123",
                "thread_url": "https://github.com/owner/repo/issues/7",
                "unit_id": "github:owner/repo:issue:7#issuecomment-123",
                "thread_id": "github:owner/repo:issue:7",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("github")
        self.bundle["plan"]["scope"]["communities"].append("Owner/Repo")
        move_source_to_platform_query(self.bundle, "src-001", "github")
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        item["thread_id"] = "github:owner/repo:issue:8"
        self.assertIn("NATIVE_ID_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_hacker_news_story_and_comment_identities_are_url_derived(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "hn",
                "community": "news.ycombinator.com",
                "source_type": "comment",
                "url": "https://news.ycombinator.com/item?id=456",
                "thread_url": "https://news.ycombinator.com/item?id=123",
                "unit_id": "hackernews:item:456",
                "thread_id": "hackernews:item:123",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("hn")
        self.bundle["plan"]["scope"]["communities"].append("news.ycombinator.com")
        move_source_to_platform_query(self.bundle, "src-001", "hn")
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        item["unit_id"] = "hackernews:item:999"
        self.assertIn("NATIVE_ID_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_hacker_news_comment_cannot_declare_itself_as_its_root_thread(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "hackernews",
                "community": "news.ycombinator.com",
                "source_type": "comment",
                "url": "https://news.ycombinator.com/item?id=456",
                "thread_url": "https://news.ycombinator.com/item?id=456",
                "unit_id": "hackernews:item:456",
                "thread_id": "hackernews:item:456",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("hackernews")
        self.bundle["plan"]["scope"]["communities"].append("news.ycombinator.com")
        move_source_to_platform_query(self.bundle, "src-001", "hackernews")
        self.assertIn("COMMENT_THREAD_SELF", issue_codes(self.analyze(), "error"))

    def test_generic_public_identity_is_the_canonical_url_and_same_host(self) -> None:
        item = source(self.bundle, "src-001")
        item.update(
            {
                "platform": "forum",
                "community": "forum.example",
                "source_type": "comment",
                "url": "https://forum.example/topics/7/comments/12",
                "thread_url": "http://forum.example/topics/7",
                "unit_id": "https://forum.example/topics/7/comments/12",
                "thread_id": "http://forum.example/topics/7",
            }
        )
        self.bundle["plan"]["scope"]["platforms"].append("forum")
        self.bundle["plan"]["scope"]["communities"].append("forum.example")
        move_source_to_platform_query(self.bundle, "src-001", "forum")
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        item["thread_url"] = "https://other.example/topics/7"
        item["thread_id"] = item["thread_url"]
        self.assertIn("THREAD_HOST_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_public_thread_count_uses_url_identity_not_declared_ids(self) -> None:
        left = source(self.bundle, "src-001")
        right = source(self.bundle, "src-002")
        right["url"] = left["thread_url"] + "c999/"
        right["thread_url"] = left["thread_url"]
        right["thread_id"] = "reddit:t3_invented"
        self.assertEqual(csr.thread_identity_key(left), csr.thread_identity_key(right))
        self.assertIn("NATIVE_ID_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_platform_rejects_lookalike_host(self) -> None:
        item = source(self.bundle, "src-001")
        item["url"] = item["url"].replace("www.reddit.com", "evidence.invalid")
        self.assertIn("PLATFORM_URL_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_nonpublic_public_source_host_is_rejected(self) -> None:
        for host in (
            "127.0.0.1",
            "127.1",
            "0177.0.0.1",
            "0x7f.0.0.1",
            "100.64.0.1",
            "192.0.0.8",
            "224.0.0.1",
            "64:ff9b:1::1",
            "2002::1",
            "home.arpa",
            "router.home.arpa",
            "forum.localdomain",
        ):
            with self.subTest(host=host):
                self.bundle = make_bundle()
                item = source(self.bundle, "src-001")
                item["platform"] = "forum"
                item["community"] = "internal"
                authority = f"[{host}]" if ":" in host else host
                item["url"] = f"https://{authority}/post/1"
                item["thread_url"] = f"https://{authority}/thread/1"
                item["unit_id"] = item["url"]
                item["thread_id"] = item["thread_url"]
                self.bundle["plan"]["scope"]["platforms"].append("forum")
                self.bundle["plan"]["scope"]["communities"].append("internal")
                move_source_to_platform_query(self.bundle, "src-001", "forum")
                self.assertIn("NONPUBLIC_URL", issue_codes(self.analyze(), "error"))

    def test_literal_ip_policy_is_version_stable_at_special_use_boundaries(self) -> None:
        blocked = (
            "192.0.0.8",
            "192.0.2.1",
            "64:ff9b:1::1",
            "2002::1",
            "3fff::1",
        )
        allowed = ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111")
        for value in blocked:
            with self.subTest(blocked=value):
                self.assertTrue(csr.is_special_use_address(csr.ipaddress.ip_address(value)))
        for value in allowed:
            with self.subTest(allowed=value):
                self.assertFalse(csr.is_special_use_address(csr.ipaddress.ip_address(value)))

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

    def test_platform_labels_on_one_generic_host_cannot_manufacture_diversity(self) -> None:
        for source_id, label, suffix in (("src-001", "forum-a", "one"), ("src-002", "forum-b", "two")):
            item = source(self.bundle, source_id)
            item.update(
                {
                    "platform": label,
                    "community": "forum.example",
                    "source_type": "comment",
                    "url": f"https://forum.example/threads/{suffix}/comment",
                    "thread_url": f"https://forum.example/threads/{suffix}",
                    "unit_id": f"https://forum.example/threads/{suffix}/comment",
                    "thread_id": f"https://forum.example/threads/{suffix}",
                }
            )
            self.bundle["plan"]["scope"]["platforms"].append(label)
            move_source_to_platform_query(self.bundle, source_id, label)
        self.bundle["plan"]["scope"]["communities"].append("forum.example")
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual(2, report["signals"][0]["platforms"])
        self.assertEqual(2, report["coverage"]["actual"]["platforms"])

    def test_query_cannot_include_sources_after_reporting_zero_results(self) -> None:
        support = self.bundle["queries"][0]
        support["results_seen"] = 0
        support["results_screened"] = 0
        support["pages_seen"] = 0
        self.assertIn("QUERY_COUNT_MISMATCH", issue_codes(self.analyze(), "error"))

    def test_duplicate_query_execution_is_rejected_after_normalization(self) -> None:
        duplicate = json.loads(json.dumps(self.bundle["queries"][0]))
        duplicate["id"] = "qry-support-copy"
        duplicate["platform"] = " ReDdIt "
        duplicate["query"] = "  RESEARCH   SKILL recurring failure workaround "
        duplicate["intent"] = "counter"
        duplicate["sort"] = "RELEVANCE AND NEW"
        duplicate["run_at"] = "2026-08-30T11:00:00+02:00"
        self.bundle["queries"].append(duplicate)
        self.assertIn("DUPLICATE_QUERY_EXECUTION", issue_codes(self.analyze(), "error"))

    def test_global_citation_reference_budget_is_enforced_once(self) -> None:
        with mock.patch.object(csr, "MAX_TOTAL_CITATION_REFERENCES", 8):
            report = self.analyze()
        budget_issues = [
            item for item in report["issues"] if item["code"] == "REFERENCE_BUDGET"
        ]
        self.assertEqual(1, len(budget_issues))

    def test_query_source_platform_and_chronology_must_match(self) -> None:
        self.bundle["queries"][0]["platform"] = "github"
        self.bundle["plan"]["scope"]["platforms"].append("github")
        self.assertIn("QUERY_PLATFORM_MISMATCH", issue_codes(self.analyze(), "error"))

        self.bundle = make_bundle()
        source(self.bundle, "src-001")["published_at"] = "2026-08-30T09:01:00Z"
        self.assertIn("QUERY_SOURCE_TIME_ORDER", issue_codes(self.analyze(), "error"))

    def test_countersearch_completion_requires_real_nontruncated_execution(self) -> None:
        counter = self.bundle["queries"][1]
        counter["truncated"] = True
        signal(self.bundle)["claimed_level"] = "recurring"
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual("partial", report["signals"][0]["countersearch_status"])
        self.assertEqual(0, report["signals"][0]["complete_counter_query_count"])

        self.bundle = make_bundle()
        counter = self.bundle["queries"][1]
        counter["results_screened"] = 0
        self.assertIn("UNSCREENED_QUERY", issue_codes(self.analyze(), "error"))

        self.bundle = make_bundle(include_counter=False)
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual("complete", report["signals"][0]["countersearch_status"])
        self.assertEqual("none_found_in_coverage", report["signals"][0]["counterevidence_level"])

    def test_counter_query_index_scans_query_ledger_once(self) -> None:
        class CountingQueries:
            def __init__(self, rows: list[dict]) -> None:
                self.rows = rows
                self.yield_count = 0

            def __iter__(self):
                for row in self.rows:
                    self.yield_count += 1
                    yield row

        rows = CountingQueries(self.bundle["queries"])
        indexed = csr.index_counter_queries_by_signal(rows)
        self.assertEqual(len(self.bundle["queries"]), rows.yield_count)
        self.assertEqual(["qry-counter"], [item["id"] for item in indexed["sig-001"]])

        original = csr.index_counter_queries_by_signal
        with mock.patch.object(csr, "index_counter_queries_by_signal", wraps=original) as indexer:
            report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        indexer.assert_called_once()

    def test_coverage_complete_null_study_can_pass_strictly(self) -> None:
        all_source_ids = [item["id"] for item in self.bundle["sources"]]
        for item in self.bundle["sources"]:
            item["stance"] = "counter"
            item["evidence_types"] = ["satisfaction"]
        self.bundle["queries"][0]["intent"] = "neutral"
        catalog_signal = signal(self.bundle)
        catalog_signal["support_citations"] = []
        catalog_signal["counter_citations"] = all_source_ids
        catalog_signal["claimed_level"] = "unsupported"
        report = self.analyze()
        self.assertEqual("pass", report["status"], report["issues"])
        self.assertEqual([], [issue for issue in report["issues"] if issue["severity"] == "warning"])
        self.assertEqual("unsupported", report["signals"][0]["calculated_level"])

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

    def test_bidi_formatting_controls_are_rejected_and_visibly_escaped(self) -> None:
        for mark in ("\u061c", "\u200e", "\u200f", "\u202e", "\u2067"):
            with self.subTest(mark=f"U+{ord(mark):04X}"):
                self.bundle = make_bundle()
                item = source(self.bundle, "src-001")
                item["captured_text"] = f"before{mark}after"
                item["excerpt"] = item["captured_text"]
                report = self.analyze()
                self.assertIn("BIDI_CONTROL", issue_codes(report, "error"))
                with self.assertRaises(ValueError):
                    csr.strict_json_loads(json.dumps({f"bad{mark}key": 1}, ensure_ascii=True))
                escaped = f"\\u{ord(mark):04x}"
                self.assertIn(escaped, csr.markdown_escape(f"before{mark}after"))
                self.assertIn(escaped, csr.terminal_safe(f"before{mark}after"))

        self.bundle = make_bundle()
        item = source(self.bundle, "src-001")
        item["captured_text"] = "hidden prefix\u202e " + item["captured_text"]
        report = self.analyze()
        self.assertIn("BIDI_CONTROL", issue_codes(report, "error"))

    def test_long_repost_chain_does_not_recurse(self) -> None:
        records = {
            f"src-{index}": {"repost_of": f"src-{index + 1}" if index < 1_999 else None}
            for index in range(2_000)
        }
        audit = csr.Audit()
        csr.detect_repost_cycles(records, audit)
        self.assertIn("REPOST_CHAIN", {issue.code for issue in audit.errors})

    def test_descending_repost_chain_build_does_not_recurse(self) -> None:
        count = 2_000
        identifiers = [f"src-{index:04d}" for index in range(count - 1, -1, -1)]
        rows = [
            {
                "id": source_id,
                "repost_of": identifiers[index + 1] if index + 1 < count else None,
                "duplicate_reviews": [],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": source_id,
                "captured_text": "",
                "published_at": "2026-01-01T00:00:00Z",
            }
            for index, source_id in enumerate(identifiers)
        ]
        by_id = {row["id"]: row for row in rows}
        audit = csr.Audit()
        csr.detect_repost_cycles(by_id, audit)
        roots, origins = csr.build_duplicate_groups(rows, by_id, audit)
        self.assertIn("REPOST_CHAIN", {issue.code for issue in audit.errors})
        self.assertEqual(count, len(roots))
        self.assertEqual(1, len(set(roots.values())))
        self.assertEqual(1, len(origins))

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

    def test_reused_private_provenance_collapses_or_rejects_conflicting_rows(self) -> None:
        base = {
            "repost_of": None,
            "duplicate_reviews": [],
            "visibility": "supplied_private",
            "platform": "export",
            "thread_id": "export:response",
            "published_at": "2026-01-01T00:00:00Z",
            "source_file_sha256": "sha256:" + "a" * 64,
            "record_ref": "survey.csv:row-7",
            "author_key": "unknown",
            "excerpt": "private response",
            "stance": "support",
            "evidence_types": ["problem"],
            "promotional": "no",
        }
        rows = [
            {**base, "id": "src-one", "unit_id": "export:one", "captured_text": "first private response"},
            {**base, "id": "src-two", "unit_id": "export:two", "captured_text": "conflicting private response"},
        ]
        by_id = {row["id"]: row for row in rows}
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, by_id, audit)
        self.assertEqual(roots["src-one"], roots["src-two"])
        self.assertIn("PRIVATE_PROVENANCE_CONFLICT", {issue.code for issue in audit.errors})

        title_rows = [
            {
                **base,
                "id": f"src-title-{index}",
                "unit_id": "export:same",
                "captured_text": "same private response",
                "title": title,
            }
            for index, title in enumerate(("First title", "Conflicting title"))
        ]
        audit = csr.Audit()
        csr.build_duplicate_groups(
            title_rows,
            {row["id"]: row for row in title_rows},
            audit,
        )
        conflict = next(issue for issue in audit.errors if issue.code == "PRIVATE_PROVENANCE_CONFLICT")
        self.assertIn("title", conflict.message)

        equivalent_rows = [
            {
                **base,
                "id": "src-equivalent-one",
                "unit_id": "export:same",
                "community": "Caf\u00e9 Support",
                "language": "EN",
                "captured_text": "same private response",
                "published_at": "2026-01-01T00:00:00Z",
                "evidence_types": ["problem", "urgency"],
            },
            {
                **base,
                "id": "src-equivalent-two",
                "unit_id": "export:same",
                "community": "cafe\u0301 support",
                "language": "en",
                "captured_text": "same private response",
                "published_at": "2026-01-01T01:00:00+01:00",
                "evidence_types": ["urgency", "problem"],
            },
        ]
        audit = csr.Audit()
        csr.build_duplicate_groups(
            equivalent_rows,
            {row["id"]: row for row in equivalent_rows},
            audit,
        )
        self.assertNotIn("PRIVATE_PROVENANCE_CONFLICT", {issue.code for issue in audit.errors})

    def test_same_private_unit_id_with_distinct_provenance_is_not_auto_collapsed(self) -> None:
        base = {
            "repost_of": None,
            "duplicate_reviews": [],
            "visibility": "supplied_private",
            "platform": "export",
            "unit_id": "export:shared-declared-unit",
            "thread_id": "export:shared-thread",
            "published_at": "2026-01-01T00:00:00Z",
        }
        rows = [
            {
                **base,
                "id": "src-one",
                "source_file_sha256": "sha256:" + "a" * 64,
                "record_ref": "survey-a.csv:row-7",
                "captured_text": "first distinct response",
            },
            {
                **base,
                "id": "src-two",
                "source_file_sha256": "sha256:" + "b" * 64,
                "record_ref": "survey-b.csv:row-9",
                "captured_text": "second unrelated answer",
            },
        ]
        by_id = {row["id"]: row for row in rows}
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, by_id, audit)
        self.assertNotEqual(roots["src-one"], roots["src-two"])
        self.assertEqual([], audit.errors)

    def test_private_duplicate_reason_does_not_claim_unit_id_collapse(self) -> None:
        shared_text = " ".join(f"shared-private-response-token-{index}" for index in range(12))
        rows = [
            {
                "id": f"src-{index}",
                "repost_of": None,
                "duplicate_reviews": [],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:shared-declared-unit",
                "thread_id": "export:thread",
                "source_file_sha256": "sha256:" + character * 64,
                "record_ref": f"survey-{index}.csv:row-1",
                "captured_text": shared_text,
                "published_at": f"2026-01-0{index + 1}T00:00:00Z",
            }
            for index, character in enumerate(("a", "b"))
        ]
        by_id = {row["id"]: row for row in rows}
        audit = csr.Audit()
        roots, origins = csr.build_duplicate_groups(rows, by_id, audit)
        groups = csr.describe_duplicate_groups(rows, roots, origins)
        self.assertEqual(roots["src-0"], roots["src-1"])
        self.assertEqual(["exact_captured_text"], groups[0]["collapse_reasons"])

    def test_public_identity_conflicting_material_is_rejected(self) -> None:
        left = json.loads(json.dumps(source(self.bundle, "src-001")))
        right = json.loads(json.dumps(left))
        left["id"] = "src-left"
        right["id"] = "src-right"
        right["stance"] = "counter"
        rows = [left, right]
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, {row["id"]: row for row in rows}, audit)
        self.assertEqual(roots["src-left"], roots["src-right"])
        self.assertIn("PUBLIC_IDENTITY_CONFLICT", {issue.code for issue in audit.errors})

    def test_case_varied_native_ids_do_not_create_duplicate_metadata_conflict(self) -> None:
        left = json.loads(json.dumps(source(self.bundle, "src-001")))
        right = json.loads(json.dumps(left))
        left["id"] = "src-left"
        right["id"] = "src-right"
        right["unit_id"] = right["unit_id"].upper()
        right["thread_id"] = right["thread_id"].upper()
        rows = [left, right]
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, {row["id"]: row for row in rows}, audit)
        self.assertEqual(roots["src-left"], roots["src-right"])
        self.assertNotIn("DUPLICATE_METADATA_CONFLICT", {issue.code for issue in audit.errors})
        self.assertNotIn("PUBLIC_IDENTITY_CONFLICT", {issue.code for issue in audit.errors})

    def test_generic_case_distinct_urls_remain_independent_end_to_end(self) -> None:
        self.bundle = make_bundle(support_count=2)
        self.bundle["plan"]["scope"]["platforms"].append("forum")
        self.bundle["plan"]["scope"]["communities"].append("forum.example")
        self.bundle["queries"][0]["platform"] = "forum"
        for source_id, url in (
            ("src-001", "https://forum.example/Topic/A"),
            ("src-002", "https://forum.example/topic/a"),
        ):
            item = source(self.bundle, source_id)
            item.update(
                {
                    "platform": "forum",
                    "community": "forum.example",
                    "source_type": "comment",
                    "url": url,
                    "thread_url": url,
                    "unit_id": url,
                    "thread_id": url,
                }
            )
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertNotIn("DUPLICATE_METADATA_CONFLICT", issue_codes(report, "error"))
        self.assertNotIn("PUBLIC_IDENTITY_CONFLICT", issue_codes(report, "error"))
        self.assertEqual(2, report["signals"][0]["support_groups"])

    def test_generic_ports_do_not_manufacture_platform_diversity(self) -> None:
        self.bundle = make_bundle(support_count=2)
        self.bundle["plan"]["scope"]["platforms"] = ["forum"]
        self.bundle["plan"]["scope"]["communities"] = ["forum.example"]
        for query_row in self.bundle["queries"]:
            query_row["platform"] = "forum"
        for index, item in enumerate(self.bundle["sources"]):
            port = 8443 + index
            url = f"https://forum.example:{port}/topic/{index}"
            item.update(
                {
                    "platform": "forum",
                    "community": "forum.example",
                    "source_type": "comment",
                    "url": url,
                    "thread_url": url,
                    "unit_id": url,
                    "thread_id": url,
                }
            )
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual(1, report["signals"][0]["platforms"])
        self.assertEqual(1, report["coverage"]["actual"]["platforms"])

    def test_native_url_aliases_collapse_without_metadata_conflicts(self) -> None:
        cases = (
            {
                "platform": "reddit",
                "community": "r/alpha",
                "source_type": "post",
                "left_url": "https://www.reddit.com/r/alpha/comments/abc/old-title/",
                "right_url": "https://www.reddit.com/r/alpha/comments/abc/new-title/",
                "unit_id": "reddit:t3_abc",
                "thread_id": "reddit:t3_abc",
            },
            {
                "platform": "github",
                "community": "Owner/Repo",
                "source_type": "issue",
                "left_url": "https://github.com/Owner/Repo/issues/7",
                "right_url": "https://github.com/owner/repo/issues/7",
                "unit_id": "github:owner/repo:issue:7",
                "thread_id": "github:owner/repo:issue:7",
            },
        )
        for case in cases:
            with self.subTest(platform=case["platform"]):
                base = json.loads(json.dumps(source(self.bundle, "src-001")))
                base.update(
                    {
                        "platform": case["platform"],
                        "community": case["community"],
                        "source_type": case["source_type"],
                        "unit_id": case["unit_id"],
                        "thread_id": case["thread_id"],
                    }
                )
                left = {**base, "id": "src-left", "url": case["left_url"], "thread_url": case["left_url"]}
                right = {**base, "id": "src-right", "url": case["right_url"], "thread_url": case["right_url"]}
                plan = json.loads(json.dumps(self.bundle["plan"]))
                plan["scope"]["platforms"] = [case["platform"]]
                plan["scope"]["communities"] = [case["community"]]
                audit = csr.Audit()
                rows, by_id = csr.validate_sources([left, right], plan, audit)
                self.assertEqual([], audit.errors)
                roots, _ = csr.build_duplicate_groups(rows, by_id, audit)
                self.assertEqual(roots["src-left"], roots["src-right"])
                self.assertNotIn("DUPLICATE_METADATA_CONFLICT", {issue.code for issue in audit.errors})
                self.assertNotIn("PUBLIC_IDENTITY_CONFLICT", {issue.code for issue in audit.errors})

    def test_exact_text_duplicate_threshold_has_boundaries(self) -> None:
        def roots_for(text: str) -> tuple[dict[str, str], csr.Audit]:
            rows = [
                {
                    "id": f"src-{index}",
                    "repost_of": None,
                    "duplicate_reviews": [],
                    "visibility": "supplied_private",
                    "platform": "export",
                    "unit_id": f"export:{index}",
                    "captured_text": text,
                    "published_at": f"2026-01-0{index + 1}T00:00:00Z",
                }
                for index in range(2)
            ]
            by_id = {row["id"]: row for row in rows}
            audit = csr.Audit()
            roots, _ = csr.build_duplicate_groups(rows, by_id, audit)
            return roots, audit

        substantive, _ = roots_for(" ".join(f"copied-workflow-token-{index}" for index in range(12)))
        self.assertEqual(substantive["src-0"], substantive["src-1"])
        short, short_audit = roots_for("same tiny phrase repeated")
        self.assertNotEqual(short["src-0"], short["src-1"])
        self.assertIn("POSSIBLE_SHORT_EXACT_DUPLICATE", {issue.code for issue in short_audit.warnings})

    def test_independent_review_suppresses_short_exact_duplicate_warning(self) -> None:
        text = "same tiny phrase repeated"
        rows = [
            {
                "id": "src-one",
                "repost_of": None,
                "duplicate_reviews": [
                    {
                        "other_source_id": "src-two",
                        "decision": "independent",
                        "reason": "Shared boilerplate only.",
                    }
                ],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:one",
                "captured_text": text,
                "published_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "src-two",
                "repost_of": None,
                "duplicate_reviews": [],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:two",
                "captured_text": text,
                "published_at": "2026-01-02T00:00:00Z",
            },
        ]
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, {row["id"]: row for row in rows}, audit)
        self.assertNotEqual(roots["src-one"], roots["src-two"])
        self.assertNotIn("POSSIBLE_SHORT_EXACT_DUPLICATE", {issue.code for issue in audit.warnings})

    def test_short_exact_duplicate_warnings_and_artifacts_are_permutation_invariant(self) -> None:
        text = "same tiny phrase repeated"
        for source_id in ("src-001", "src-002", "src-003"):
            item = source(self.bundle, source_id)
            item["captured_text"] = text
            item["excerpt"] = text
        self.bundle["notes"]["observations"][0]["text"] = text

        original = json.loads(json.dumps(self.bundle))
        permuted = json.loads(json.dumps(self.bundle))
        permuted["sources"] = list(reversed(permuted["sources"]))
        studies = [self.study / "original", self.study / "permuted"]
        reports: list[dict] = []
        contexts: list[dict] = []
        artifacts: list[dict[str, bytes]] = []
        for study, bundle in zip(studies, (original, permuted)):
            write_bundle(study, bundle)
            committed, code = csr.build_artifacts(study)
            self.assertEqual(0, code, committed)
            report, context = csr.analyze(study)
            reports.append(report)
            contexts.append(context)
            artifacts.append(artifact_bytes(study))

        warning_paths = [
            issue["path"]
            for issue in reports[0]["issues"]
            if issue["code"] == "POSSIBLE_SHORT_EXACT_DUPLICATE"
        ]
        self.assertEqual(
            [
                "source-ledger.jsonl:src-001/src-002",
            ],
            warning_paths,
        )
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(contexts[0]["duplicate_root"], contexts[1]["duplicate_root"])
        self.assertEqual(contexts[0]["origin_by_root"], contexts[1]["origin_by_root"])
        self.assertEqual(artifacts[0], artifacts[1])

    def test_short_exact_independence_reviews_are_not_transitive(self) -> None:
        text = "same tiny phrase repeated"
        rows = [
            {
                "id": source_id,
                "repost_of": None,
                "duplicate_reviews": (
                    [
                        {
                            "other_source_id": other_id,
                            "decision": "independent",
                            "reason": "Pair was checked directly.",
                        }
                        for other_id in ("src-b", "src-c")
                    ]
                    if source_id == "src-a"
                    else []
                ),
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": f"export:{source_id}",
                "captured_text": text,
                "published_at": f"2026-01-0{index}T00:00:00Z",
            }
            for index, source_id in enumerate(("src-a", "src-b", "src-c"), start=1)
        ]
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, {row["id"]: row for row in rows}, audit)
        self.assertEqual(3, len(set(roots.values())))
        warnings = [issue for issue in audit.warnings if issue.code == "POSSIBLE_SHORT_EXACT_DUPLICATE"]
        self.assertEqual(1, len(warnings))
        self.assertEqual("source-ledger.jsonl:src-b/src-c", warnings[0].path)

    def test_later_transitive_same_source_union_suppresses_short_exact_warning(self) -> None:
        text = "same tiny phrase repeated"
        rows = [
            {
                "id": "src-a",
                "repost_of": None,
                "duplicate_reviews": [],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:a",
                "captured_text": text,
                "published_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "src-b",
                "repost_of": None,
                "duplicate_reviews": [],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:b",
                "captured_text": text,
                "published_at": "2026-01-02T00:00:00Z",
            },
            {
                "id": "src-z-bridge",
                "repost_of": "src-a",
                "duplicate_reviews": [
                    {
                        "other_source_id": "src-b",
                        "decision": "same_source",
                        "reason": "The bridge record joins both captures.",
                    }
                ],
                "visibility": "supplied_private",
                "platform": "export",
                "unit_id": "export:bridge",
                "captured_text": "different bridge record",
                "published_at": "2026-01-03T00:00:00Z",
            },
        ]
        audit = csr.Audit()
        roots, _ = csr.build_duplicate_groups(rows, {row["id"]: row for row in rows}, audit)
        self.assertEqual(roots["src-a"], roots["src-b"])
        self.assertEqual(roots["src-a"], roots["src-z-bridge"])
        self.assertNotIn("POSSIBLE_SHORT_EXACT_DUPLICATE", {issue.code for issue in audit.warnings})

    def test_invalid_private_visibility_does_not_change_fingerprint_with_text(self) -> None:
        def fingerprint(private_text: str) -> str:
            private = {
                "id": "src-private",
                "visibility": "supplied-private",
                "title": private_text,
                "captured_text": private_text,
                "excerpt": private_text,
                "notes": private_text,
                "record_ref": "survey.csv:row-7",
                "source_file_sha256": "sha256:" + "a" * 64,
            }
            return csr.semantic_fingerprint({}, [], [private], [], {})

        self.assertEqual(fingerprint("yes"), fingerprint("no"))

    def test_unknown_private_fields_do_not_change_public_fingerprint(self) -> None:
        def fingerprint(typo_value: str) -> str:
            private = {
                "id": "src-private",
                "visibility": "supplied_private",
                "title": "withheld",
                "captured_text": "withheld",
                "excerpt": "withheld",
                "notes": "withheld",
                "record_ref": "survey.csv:row-7",
                "source_file_sha256": "sha256:" + "a" * 64,
                "captured_txt": typo_value,
            }
            return csr.semantic_fingerprint({}, [], [private], [], {})

        self.assertEqual(fingerprint("yes"), fingerprint("no"))

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

    def test_fuzzy_preparation_has_a_deterministic_storage_budget(self) -> None:
        rows = [
            {
                "id": f"src-{row}",
                "captured_text": " ".join(f"token-{row}-{index}" for index in range(80)),
                "duplicate_reviews": [],
            }
            for row in range(2)
        ]
        roots = {row["id"]: row["id"] for row in rows}
        audit = csr.Audit()
        with mock.patch.object(csr, "MAX_FUZZY_STORED_SHINGLES", 100):
            csr.detect_fuzzy_duplicates(rows, roots, audit)
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

    def test_extreme_offset_timestamps_return_structured_errors_not_tracebacks(self) -> None:
        for timestamp in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(timestamp=timestamp):
                self.bundle = make_bundle()
                source(self.bundle, "src-001")["published_at"] = timestamp
                write_bundle(self.study, self.bundle)
                result = run_cli("validate", str(self.study), "--json")
                payload = json.loads(result.stdout)
                self.assertEqual(1, result.returncode)
                self.assertIn("TIMESTAMP", issue_codes(payload, "error"))
                self.assertNotIn("Traceback", result.stderr)

    def test_date_and_timestamp_grammar_is_pinned_across_python_versions(self) -> None:
        for value in (
            "2026-08-30T12:00:00Z",
            "2026-08-30T12:00:00.123456+02:00",
        ):
            with self.subTest(valid=value):
                audit = csr.Audit()
                self.assertIsNotNone(csr.parse_datetime(value, "timestamp", audit))
                self.assertEqual([], audit.errors)

        for value in (
            "20260830T120000+00:00",
            "2026-W35-7T12:00:00+00:00",
            "2026-08-30T12:00:00,5+00:00",
            "2026-08-30 12:00:00+00:00",
        ):
            with self.subTest(invalid=value):
                audit = csr.Audit()
                self.assertIsNone(csr.parse_datetime(value, "timestamp", audit))
                self.assertEqual({"TIMESTAMP"}, {issue.code for issue in audit.errors})

        for value in ("20260830", "2026-W35-7"):
            with self.subTest(invalid_date=value):
                audit = csr.Audit()
                self.assertIsNone(csr.parse_date(value, "date", audit))
                self.assertEqual({"DATE"}, {issue.code for issue in audit.errors})

    def test_unicode_normalization_is_pinned_across_python_versions(self) -> None:
        # U+A7F2 is unassigned in the pinned Unicode 3.2 database. Host NFKC
        # changed from leaving it untouched in Python 3.10 to mapping it to
        # ASCII C in Python 3.14, so this is a cross-runtime regression marker.
        self.assertEqual("3.2.0", csr.PINNED_UNICODE.unidata_version)
        self.assertEqual("\ua7f2", csr.normalize_text("\ua7f2"))
        self.assertEqual("alice smith", csr.normalized_label("ＡＬＩＣＥ　ＳＭＩＴＨ"))
        self.assertEqual("one two", csr.normalize_text("\tone\u00a0\u2003two\n"))
        self.assertEqual("", csr.strip_pinned_whitespace("\u180e"))
        self.assertEqual("\U00011f50", csr.strip_pinned_whitespace("\U00011f50"))

    def test_privacy_regex_classes_are_pinned_across_python_versions(self) -> None:
        # These code points changed word/digit classification across supported
        # runtimes. Privacy decisions must use the explicit ASCII policy.
        self.assertIsNotNone(csr.EMAIL_RE.search("\ua7f2foo@example.com"))
        self.assertIsNotNone(csr.PHONE_RE.search("123456789\u1c89"))
        self.assertIsNone(csr.PHONE_RE.search("\U00011f50" * 9))

    def test_oversized_lists_are_capped_before_returning_validation_context(self) -> None:
        self.bundle["notes"]["coverage_notes"] = [f"note-{index}" for index in range(csr.MAX_LIST_ITEMS + 1)]
        report, context = analyze_bundle(self.study, self.bundle)
        self.assertIn("LIST_TOO_LARGE", issue_codes(report, "error"))
        self.assertEqual(csr.MAX_LIST_ITEMS, len(context["notes"]["coverage_notes"]))

    def test_init_placeholders_cannot_be_mistaken_for_completed_research(self) -> None:
        initialized = run_cli(
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
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        result = run_cli("validate", str(self.study), "--json")
        payload = json.loads(result.stdout)
        self.assertIn("INCOMPLETE_RESEARCH", issue_codes(payload, "error"))
        self.assertIn("EMPTY_LIST", issue_codes(payload, "error"))

    def test_init_as_of_uses_the_pinned_calendar_date_grammar(self) -> None:
        for index, invalid_date in enumerate(("20260830", "2026-W35-7", "2026-8-30")):
            with self.subTest(invalid_date=invalid_date):
                target = self.study.with_name(f"invalid-init-date-{index}")
                result = run_cli(
                    "init",
                    str(target),
                    "--question",
                    "Which evidence recurs?",
                    "--decision",
                    "Choose a follow-up test.",
                    "--mode",
                    "quick",
                    "--as-of",
                    invalid_date,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("YYYY-MM-DD", result.stderr)
                self.assertFalse((target / "study-plan.json").exists())

    def test_deep_json_and_jsonl_return_structured_depth_errors(self) -> None:
        write_bundle(self.study, self.bundle)
        deep = "[" * (csr.MAX_JSON_DEPTH + 1) + "0" + "]" * (csr.MAX_JSON_DEPTH + 1)
        (self.study / "study-plan.json").write_text(deep, encoding="utf-8")
        result = run_cli("validate", str(self.study), "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertIn("JSON_DEPTH", issue_codes(payload, "error"))
        self.assertNotIn("Traceback", result.stderr)

        write_bundle(self.study, self.bundle)
        (self.study / "query-log.jsonl").write_text(deep + "\n", encoding="utf-8")
        result = run_cli("validate", str(self.study), "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertIn("JSON_DEPTH", issue_codes(payload, "error"))
        self.assertNotIn("Traceback", result.stderr)

    def test_json_depth_scanner_ignores_brackets_inside_strings(self) -> None:
        value = json.dumps({"text": "[" * (csr.MAX_JSON_DEPTH + 10)})
        self.assertEqual({"text": "[" * (csr.MAX_JSON_DEPTH + 10)}, csr.strict_json_loads(value))

    def test_successful_build_json_stdout_is_one_json_document(self) -> None:
        write_bundle(self.study, self.bundle)
        built = run_cli("build", str(self.study), "--json")
        self.assertEqual(0, built.returncode, built.stderr)
        parsed = json.loads(built.stdout)
        self.assertEqual("pass", parsed["status"])

    def test_json_cli_is_ascii_safe_under_legacy_stdio_encoding(self) -> None:
        signal(self.bundle)["name"] = "Café research"
        write_bundle(self.study, self.bundle)
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "ascii"
        result = subprocess.run(
            [sys.executable, str(csr.__file__), "validate", str(self.study), "--json"],
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode("ascii", errors="replace"))
        payload = json.loads(result.stdout.decode("ascii"))
        self.assertEqual("Café research", payload["signals"][0]["name"])


class FilesystemSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.study = self.root / "study"
        self.bundle = make_bundle()
        write_bundle(self.study, self.bundle)

    def tearDown(self) -> None:
        self._temporary.cleanup()

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
        self.assertTrue(csr.is_link_or_reparse(link))
        self.assertFalse(link.is_symlink())

    def copy_artifact_set(self, source_dir: Path, target_dir: Path) -> None:
        target_dir.mkdir()
        for name in csr.ARTIFACT_NAMES:
            (target_dir / name).write_bytes((source_dir / name).read_bytes())

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

    def test_generated_artifact_byte_limit_is_enforced(self) -> None:
        report, context = csr.analyze(self.study)
        self.assertEqual("pass", report["status"])
        with mock.patch.object(csr, "MAX_GENERATED_ARTIFACT_BYTES", 16):
            with self.assertRaisesRegex(ValueError, "artifact safety limit"):
                csr.artifact_contents(report, context)

    def test_findings_renderer_stops_at_byte_cap_before_all_citations(self) -> None:
        report, context = csr.analyze(self.study)
        self.assertEqual("pass", report["status"])
        source_id = report["signals"][0]["support_source_ids"][0]
        report["signals"][0]["support_source_ids"] = [source_id] * 100
        context["source_by_id"][source_id]["url"] = "https://forum.example/" + "x" * 2_000
        with (
            mock.patch.object(csr, "MAX_GENERATED_ARTIFACT_BYTES", 8 * 1024),
            mock.patch.object(csr, "render_source", wraps=csr.render_source) as render_source,
        ):
            with self.assertRaisesRegex(ValueError, "findings.md.*artifact safety limit"):
                csr.render_findings(report, context)
        self.assertLess(render_source.call_count, 100)

    def test_expected_artifact_name_directory_is_refused_and_preserved(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        target = self.study / "artifacts" / "findings.md"
        target.unlink()
        target.mkdir()
        nested = target / "user-data.txt"
        nested.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "regular file"):
            csr.build_artifacts(self.study)
        self.assertEqual("preserve", nested.read_text(encoding="utf-8"))

    def test_strict_audit_rejects_unexpected_artifact_directory(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        unexpected = self.study / "artifacts" / "raw-private-ledger"
        unexpected.mkdir()
        (unexpected / "source-ledger.jsonl").write_text("private", encoding="utf-8")
        current, context = csr.analyze(self.study)
        issues = csr.audit_artifacts(self.study, current, context)
        self.assertIn("EXTRA_ARTIFACT", {issue.code for issue in issues})

    def test_artifact_extra_entry_reporting_is_bounded(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        for index in range(20):
            (self.study / "artifacts" / f"extra-{index}.txt").write_text("x", encoding="utf-8")
        current, context = csr.analyze(self.study)
        issues = csr.audit_artifacts(self.study, current, context)
        extra_issues = [issue for issue in issues if issue.code == "EXTRA_ARTIFACT"]
        self.assertGreaterEqual(len(extra_issues), 1)
        self.assertLessEqual(len(extra_issues), len(csr.ARTIFACT_NAMES) + 1)

    def test_recovery_study_directory_scan_is_bounded(self) -> None:
        empty_study = self.root / "bounded-recovery"
        empty_study.mkdir()
        for index in range(3):
            (empty_study / f"entry-{index}.txt").write_text("x", encoding="utf-8")
        with mock.patch.object(csr, "MAX_STUDY_DIRECTORY_ENTRIES", 2):
            with self.assertRaisesRegex(ValueError, "recovery scan limit"):
                csr.recover_interrupted_artifact_swap(empty_study)

    def test_recovery_cannot_delete_an_active_concurrent_build_stage(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        stage_ready = threading.Event()
        resume_build = threading.Event()
        original_write = csr.atomic_write_text
        outcome: dict[str, object] = {}

        def pause_after_first_stage_write(path: Path, content: str) -> None:
            original_write(path, content)
            if path.parent.name.startswith(".csr-artifacts-stage-") and path.name == "signals.csv":
                stage_ready.set()
                if not resume_build.wait(10):
                    raise TimeoutError("test did not release the paused build")

        def run_build() -> None:
            try:
                outcome["result"] = csr.build_artifacts(self.study)
            except BaseException as exc:  # pragma: no cover - asserted below
                outcome["error"] = exc

        with mock.patch.object(csr, "atomic_write_text", side_effect=pause_after_first_stage_write):
            worker = threading.Thread(target=run_build, daemon=True)
            worker.start()
            self.assertTrue(stage_ready.wait(10), "concurrent build never reached its staged write")
            stages = list(self.study.glob(".csr-artifacts-stage-*"))
            self.assertEqual(1, len(stages))
            self.assertTrue((stages[0] / "signals.csv").is_file())
            with self.assertRaisesRegex(ValueError, "active for this study"):
                csr.recover_interrupted_artifact_swap(self.study)
            self.assertTrue((stages[0] / "signals.csv").is_file())
            resume_build.set()
            worker.join(10)

        self.assertFalse(worker.is_alive(), "concurrent build did not finish")
        self.assertNotIn("error", outcome)
        committed, exit_code = outcome["result"]  # type: ignore[misc]
        self.assertEqual(0, exit_code, committed)
        self.assertEqual(set(csr.ARTIFACT_NAMES), {path.name for path in (self.study / "artifacts").iterdir()})
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_completed_stage_is_revalidated_immediately_before_install(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        original_write = csr.atomic_write_text

        def remove_predecessor_after_last_write(path: Path, content: str) -> None:
            original_write(path, content)
            if path.parent.name.startswith(".csr-artifacts-stage-") and path.name == "audit.json":
                (path.parent / "signals.csv").unlink()

        with mock.patch.object(csr, "atomic_write_text", side_effect=remove_predecessor_after_last_write):
            with self.assertRaisesRegex(ValueError, "Completed artifact stage"):
                csr.build_artifacts(self.study)
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_installed_set_is_revalidated_before_backup_deletion(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        original_validate = csr._validate_exact_artifact_directory

        def change_live_set_before_cleanup(directory: Path, study_dir: Path, message: str) -> None:
            if directory.name == "artifacts" and message == "Installed artifact directory changed before backup cleanup":
                (directory / "signals.csv").unlink()
            original_validate(directory, study_dir, message)

        with mock.patch.object(csr, "_validate_exact_artifact_directory", side_effect=change_live_set_before_cleanup):
            with self.assertRaisesRegex(ValueError, "changed before backup cleanup"):
                csr.build_artifacts(self.study)
        backups = list(self.study.glob(".csr-artifacts-backup-*"))
        self.assertEqual(1, len(backups))
        self.assertEqual(prior, {name: (backups[0] / name).read_bytes() for name in csr.ARTIFACT_NAMES})
        self.assertFalse((self.study / "artifacts" / "signals.csv").exists())

    def test_artifact_audit_rejects_sparse_oversize_before_reading_content(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        oversized = self.study / "artifacts" / "findings.md"
        with oversized.open("r+b") as stream:
            stream.truncate(1 << 30)
        current, context = csr.analyze(self.study)
        issues = csr.audit_artifacts(self.study, current, context)
        self.assertIn("MODIFIED_ARTIFACT", {issue.code for issue in issues})

    def test_next_build_can_recover_a_hard_stop_between_directory_swaps(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        token = "f" * 32
        backup = self.study / f".csr-artifacts-backup-{token}"
        stage = self.study / f".csr-artifacts-stage-{token}"
        stage.mkdir()
        for name, data in prior.items():
            (stage / name).write_bytes(data)
        os.replace(self.study / "artifacts", backup)
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertEqual(prior, artifact_bytes(self.study))
        self.assertFalse(backup.exists())
        self.assertFalse(stage.exists())

    def test_recovery_cleans_partial_generated_stage_before_or_during_install(self) -> None:
        token = "a" * 32
        stage = self.study / f".csr-artifacts-stage-{token}"
        stage.mkdir()
        (stage / "signals.csv").write_text("partial", encoding="utf-8")
        (stage / ".csr-interrupted.tmp").write_text("partial", encoding="utf-8")
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertFalse(stage.exists())
        self.assertFalse((self.study / "artifacts").exists())

        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)
        live_stage = self.study / f".csr-artifacts-stage-{'b' * 32}"
        live_stage.mkdir()
        (live_stage / "signals.csv").write_text("partial", encoding="utf-8")
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertFalse(live_stage.exists())
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_recovery_cleans_post_install_backup_so_a_later_gap_can_recover(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        prior = artifact_bytes(self.study)

        first_token = "c" * 32
        first_backup = self.study / f".csr-artifacts-backup-{first_token}"
        installed_stage = self.study / f".csr-artifacts-stage-{first_token}"
        self.copy_artifact_set(self.study / "artifacts", installed_stage)
        os.replace(self.study / "artifacts", first_backup)
        os.replace(installed_stage, self.study / "artifacts")
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertFalse(first_backup.exists())
        self.assertEqual(prior, artifact_bytes(self.study))

        second_token = "d" * 32
        second_backup = self.study / f".csr-artifacts-backup-{second_token}"
        second_stage = self.study / f".csr-artifacts-stage-{second_token}"
        self.copy_artifact_set(self.study / "artifacts", second_stage)
        os.replace(self.study / "artifacts", second_backup)
        self.assertTrue(csr.recover_interrupted_artifact_swap(self.study))
        self.assertFalse(second_backup.exists())
        self.assertFalse(second_stage.exists())
        self.assertEqual(prior, artifact_bytes(self.study))

    def test_recovery_never_deletes_an_unmatched_prefix_directory(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        backup = self.study / ".csr-artifacts-backup-transaction"
        unrelated = self.study / ".csr-artifacts-stage-unrelated"
        unrelated.mkdir()
        retained = unrelated / "researcher-data.txt"
        retained.write_text("keep", encoding="utf-8")
        os.replace(self.study / "artifacts", backup)
        self.assertFalse(csr.recover_interrupted_artifact_swap(self.study))
        self.assertTrue(backup.exists())
        self.assertFalse((self.study / "artifacts").exists())
        self.assertEqual("keep", retained.read_text(encoding="utf-8"))

    def test_recovery_rejects_partial_matching_stage_and_backup(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        token = "e" * 32
        backup = self.study / f".csr-artifacts-backup-{token}"
        stage = self.study / f".csr-artifacts-stage-{token}"
        stage.mkdir()
        (stage / "signals.csv").write_text("partial", encoding="utf-8")
        os.replace(self.study / "artifacts", backup)
        with self.assertRaisesRegex(ValueError, "not an exact generated-artifact set"):
            csr.recover_interrupted_artifact_swap(self.study)
        self.assertTrue(backup.exists())
        self.assertTrue(stage.exists())
        self.assertFalse((self.study / "artifacts").exists())

        (stage / "signals.csv").unlink()
        (backup / "findings.md").unlink()
        with self.assertRaisesRegex(ValueError, "exactly the three regular"):
            csr.recover_interrupted_artifact_swap(self.study)
        self.assertTrue(backup.exists())

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

    def test_windows_junctions_are_rejected_for_study_atomic_and_artifact_paths(self) -> None:
        target = self.root / "junction-target"
        target.mkdir()
        study_link = self.root / "study-junction"
        self.create_junction(study_link, target)
        self.assertTrue(csr.is_link_or_reparse(study_link))
        with mock.patch.object(Path, "is_junction", None, create=True):
            self.assertTrue(csr.is_link_or_reparse(study_link), "Python 3.10/3.11 lstat fallback")
        with self.assertRaisesRegex(ValueError, "reparse-point study"):
            csr.ensure_study_dir(study_link)
        with self.assertRaisesRegex(OSError, "reparse-point directory"):
            csr.atomic_write_text(study_link / "must-not-write.txt", "unsafe")
        self.assertFalse((target / "must-not-write.txt").exists())

        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        artifact_payload = self.root / "artifact-payload"
        os.replace(self.study / "artifacts", artifact_payload)
        self.create_junction(self.study / "artifacts", artifact_payload)
        with self.assertRaisesRegex(ValueError, "reparse-point artifact directory"):
            csr.safe_artifacts_dir(self.study, create=False)

    def test_windows_junction_recovery_candidate_is_refused_and_preserved(self) -> None:
        report, code = csr.build_artifacts(self.study)
        self.assertEqual(0, code, report)
        token = "e" * 32
        payload = self.root / "junction-backup-payload"
        os.replace(self.study / "artifacts", payload)
        backup = self.study / f".csr-artifacts-backup-{token}"
        self.create_junction(backup, payload)
        stage = self.study / f".csr-artifacts-stage-{token}"
        self.copy_artifact_set(payload, stage)
        with self.assertRaisesRegex(ValueError, "exactly the three regular"):
            csr.recover_interrupted_artifact_swap(self.study)
        self.assertTrue(csr.is_link_or_reparse(backup))
        self.assertTrue(stage.exists())
        self.assertFalse((self.study / "artifacts").exists())


if __name__ == "__main__":
    unittest.main()
