from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support import (
    csr,
    issue_codes,
    make_bundle,
    query,
    select_signal_sources,
    set_reddit_thread,
    signal,
    source,
    write_bundle,
)


class StudyCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.study = Path(self._temporary.name) / "study"
        self.bundle = make_bundle()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def analyze(self) -> dict:
        write_bundle(self.study, self.bundle)
        report, _ = csr.analyze(self.study)
        return report

    def metric(self) -> dict:
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        return report["signals"][0]


class EvidenceThresholdTests(StudyCase):
    def test_recurring_exactly_three_authors_across_two_threads(self) -> None:
        selected = ["src-001", "src-002", "src-003"]
        select_signal_sources(self.bundle, selected, ["src-900"])
        set_reddit_thread(source(self.bundle, "src-003"), "thread1")
        signal(self.bundle)["claimed_level"] = "recurring"
        metric = self.metric()
        self.assertEqual(3, metric["distinct_author_keys"])
        self.assertEqual(2, metric["distinct_threads"])
        self.assertEqual("recurring", metric["calculated_level"])

    def test_two_authors_cannot_be_called_recurring(self) -> None:
        selected = ["src-001", "src-002", "src-003"]
        select_signal_sources(self.bundle, selected, ["src-900"])
        source(self.bundle, "src-003")["author_key"] = source(self.bundle, "src-002")["author_key"]
        signal(self.bundle)["claimed_level"] = "anecdotal"
        metric = self.metric()
        self.assertEqual(2, metric["distinct_author_keys"])
        self.assertEqual("anecdotal", metric["calculated_level"])

    def test_one_thread_cannot_be_called_recurring_even_with_many_authors(self) -> None:
        selected = [f"src-{index:03d}" for index in range(1, 9)]
        for source_id in selected:
            set_reddit_thread(source(self.bundle, source_id), "oneviral")
        signal(self.bundle)["claimed_level"] = "anecdotal"
        metric = self.metric()
        self.assertEqual(8, metric["distinct_author_keys"])
        self.assertEqual(1, metric["distinct_threads"])
        self.assertEqual("anecdotal", metric["calculated_level"])

    def test_well_corroborated_exact_boundary(self) -> None:
        selected = [f"src-{index:03d}" for index in range(1, 7)]
        select_signal_sources(self.bundle, selected, ["src-900"])
        for index, source_id in enumerate(selected):
            item = source(self.bundle, source_id)
            set_reddit_thread(item, f"boundary{min(index, 3)}")
            item["evidence_types"] = ["workaround"] if index < 3 else ["adoption"]
        signal(self.bundle)["claimed_level"] = "well-corroborated"
        metric = self.metric()
        self.assertEqual(6, metric["distinct_author_keys"])
        self.assertEqual(4, metric["distinct_threads"])
        self.assertEqual(2, metric["communities"])
        self.assertEqual(["adoption", "workaround"], metric["costly_behavior_types"])
        self.assertEqual("well-corroborated", metric["calculated_level"])

    def test_well_corroborated_allows_exactly_twenty_five_percent_promotion_risk(self) -> None:
        source(self.bundle, "src-007")["promotional"] = "unclear"
        source(self.bundle, "src-008")["promotional"] = "unclear"
        signal(self.bundle)["claimed_level"] = "well-corroborated"
        metric = self.metric()
        self.assertEqual(0.25, metric["promotion_risk_share"])
        self.assertEqual(6, metric["support_groups"])
        self.assertEqual("well-corroborated", metric["calculated_level"])

    def test_overclaim_is_a_hard_error(self) -> None:
        select_signal_sources(self.bundle, ["src-001", "src-002"], ["src-900"])
        signal(self.bundle)["claimed_level"] = "recurring"
        report = self.analyze()
        self.assertIn("OVERCLAIMED_LEVEL", issue_codes(report, "error"))

    def test_zero_eligible_support_is_unsupported(self) -> None:
        select_signal_sources(self.bundle, [], ["src-900"])
        signal(self.bundle)["claimed_level"] = "unsupported"
        metric = self.metric()
        self.assertEqual(0, metric["support_groups"])
        self.assertEqual("unsupported", metric["calculated_level"])
        self.assertEqual(0.0, metric["evidence_score"])

    def test_viral_engagement_never_changes_score_or_level(self) -> None:
        for index in range(1, 9):
            item = source(self.bundle, f"src-{index:03d}")
            set_reddit_thread(item, "viral")
        signal(self.bundle)["claimed_level"] = "anecdotal"
        low = self.metric()
        for index in range(1, 9):
            item = source(self.bundle, f"src-{index:03d}")
            item["engagement"]["score"] = 10_000_000
            item["engagement"]["comments"] = 10_000_000
        high = self.metric()
        self.assertEqual(low["evidence_score"], high["evidence_score"])
        self.assertEqual(low["calculated_level"], high["calculated_level"])


class PromotionPolicyTests(StudyCase):
    def test_promotion_no_is_eligible(self) -> None:
        metric = self.metric()
        self.assertEqual(8, metric["support_groups"])

    def test_promotion_yes_is_display_only(self) -> None:
        source(self.bundle, "src-001")["promotional"] = "yes"
        signal(self.bundle)["claimed_level"] = "well-corroborated"
        metric = self.metric()
        self.assertEqual(7, metric["support_groups"])
        self.assertEqual(0.125, metric["promotion_risk_share"])

    def test_promotion_unclear_is_ineligible_and_penalized_beyond_yes(self) -> None:
        source(self.bundle, "src-001")["promotional"] = "yes"
        yes_metric = self.metric()
        source(self.bundle, "src-001")["promotional"] = "unclear"
        unclear_metric = self.metric()
        self.assertEqual(yes_metric["support_groups"], unclear_metric["support_groups"])
        self.assertLess(unclear_metric["evidence_score"], yes_metric["evidence_score"])

    def test_invalid_promotion_value_is_rejected(self) -> None:
        source(self.bundle, "src-001")["promotional"] = "maybe"
        report = self.analyze()
        self.assertIn("ENUM", issue_codes(report, "error"))


class DuplicateAndRepostTests(StudyCase):
    def test_explicit_repost_collapses_to_one_group(self) -> None:
        source(self.bundle, "src-002")["repost_of"] = "src-001"
        select_signal_sources(
            self.bundle,
            ["src-001", "src-003", "src-004", "src-005", "src-006", "src-007", "src-008"],
            ["src-900"],
        )
        signal(self.bundle)["claimed_level"] = "well-corroborated"
        report = self.analyze()
        self.assertEqual("pass", report["status"], report["issues"])
        self.assertEqual(8, report["counts"]["duplicate_groups"])
        self.assertEqual(7, report["signals"][0]["support_groups"])

    def test_exact_long_text_collapses_independent_urls(self) -> None:
        shared = " ".join(f"distinct-token-{index}" for index in range(20))
        source(self.bundle, "src-001")["captured_text"] = shared
        source(self.bundle, "src-001")["excerpt"] = " ".join(shared.split()[:5])
        source(self.bundle, "src-002")["captured_text"] = shared
        source(self.bundle, "src-002")["excerpt"] = " ".join(shared.split()[:5])
        select_signal_sources(
            self.bundle,
            ["src-001", "src-003", "src-004", "src-005", "src-006", "src-007", "src-008"],
            ["src-900"],
        )
        report = self.analyze()
        self.assertNotEqual("fail", report["status"], report["issues"])
        self.assertEqual(8, report["counts"]["duplicate_groups"])

    def test_same_source_review_collapses_pair(self) -> None:
        source(self.bundle, "src-002")["duplicate_reviews"] = [
            {
                "other_source_id": "src-001",
                "decision": "same_source",
                "reason": "Manual comparison confirms the same copied source unit.",
            }
        ]
        select_signal_sources(
            self.bundle,
            ["src-001", "src-003", "src-004", "src-005", "src-006", "src-007", "src-008"],
            ["src-900"],
        )
        report = self.analyze()
        self.assertEqual(8, report["counts"]["duplicate_groups"])

    def test_repost_cycle_is_rejected(self) -> None:
        source(self.bundle, "src-001")["repost_of"] = "src-002"
        source(self.bundle, "src-002")["repost_of"] = "src-001"
        report = self.analyze()
        self.assertIn("REPOST_CYCLE", issue_codes(report, "error"))

    def test_repost_chain_is_rejected(self) -> None:
        source(self.bundle, "src-002")["repost_of"] = "src-001"
        source(self.bundle, "src-003")["repost_of"] = "src-002"
        report = self.analyze()
        self.assertIn("REPOST_CHAIN", issue_codes(report, "error"))

    def test_missing_repost_target_is_rejected(self) -> None:
        source(self.bundle, "src-002")["repost_of"] = "src-missing"
        report = self.analyze()
        self.assertIn("BAD_REPOST_REF", issue_codes(report, "error"))

    def test_non_origin_citation_is_rejected(self) -> None:
        source(self.bundle, "src-002")["repost_of"] = "src-001"
        report = self.analyze()
        self.assertIn("NON_ORIGIN_CITATION", issue_codes(report, "error"))

    def test_conflicting_duplicate_reviews_are_rejected(self) -> None:
        source(self.bundle, "src-001")["duplicate_reviews"] = [
            {"other_source_id": "src-002", "decision": "same_source", "reason": "Same unit."}
        ]
        source(self.bundle, "src-002")["duplicate_reviews"] = [
            {"other_source_id": "src-001", "decision": "independent", "reason": "Separate account."}
        ]
        report = self.analyze()
        self.assertIn("CONFLICTING_DUPLICATE_REVIEW", issue_codes(report, "error"))

    def test_canonical_url_metadata_conflict_is_rejected(self) -> None:
        self.bundle["plan"]["scope"]["platforms"].append("forum")
        self.bundle["plan"]["scope"]["communities"].append("forum.example")
        for source_id in ("src-001", "src-002"):
            item = source(self.bundle, source_id)
            item["platform"] = "forum"
            item["community"] = "forum.example"
            item["url"] = "https://forum.example/posts/shared"
            item["thread_url"] = "https://forum.example/threads/one"
            item["thread_id"] = "forum:thread-one"
        source(self.bundle, "src-001")["unit_id"] = "forum:unit-one"
        source(self.bundle, "src-002")["unit_id"] = "forum:unit-two"
        report = self.analyze()
        self.assertIn("DUPLICATE_METADATA_CONFLICT", issue_codes(report, "error"))


class WillingnessToPayTests(StudyCase):
    def test_explicit_purchase_intent_supports_anecdotal_wtp(self) -> None:
        source(self.bundle, "src-001")["evidence_types"] = ["purchase_intent"]
        signal(self.bundle)["wtp_statement"] = "One source explicitly states purchase intent."
        signal(self.bundle)["wtp_citations"] = ["src-001"]
        metric = self.metric()
        self.assertEqual("anecdotal", metric["wtp_status"])
        self.assertEqual("purchase_intent", metric["wtp_evidence"])

    def test_recurring_wtp_requires_three_authors_and_two_threads(self) -> None:
        for source_id in ("src-001", "src-002", "src-003"):
            source(self.bundle, source_id)["evidence_types"] = ["purchase_intent"]
        source(self.bundle, "src-003")["evidence_types"] = ["observed_payment"]
        signal(self.bundle)["wtp_statement"] = "Three independent sources explicitly describe purchase behavior."
        signal(self.bundle)["wtp_citations"] = ["src-001", "src-002", "src-003"]
        metric = self.metric()
        self.assertEqual("recurring", metric["wtp_status"])
        self.assertEqual("mixed", metric["wtp_evidence"])
        self.assertEqual(3, metric["wtp_authors"])
        self.assertGreaterEqual(metric["wtp_threads"], 2)

    def test_pain_is_not_wtp(self) -> None:
        source(self.bundle, "src-001")["evidence_types"] = ["problem", "urgency"]
        signal(self.bundle)["wtp_statement"] = "Improperly inferred willingness to pay."
        signal(self.bundle)["wtp_citations"] = ["src-001"]
        report = self.analyze()
        self.assertIn("UNSUPPORTED_WTP", issue_codes(report, "error"))

    def test_promotional_or_unclear_source_cannot_support_wtp(self) -> None:
        source(self.bundle, "src-001")["evidence_types"] = ["observed_payment"]
        source(self.bundle, "src-001")["promotional"] = "unclear"
        signal(self.bundle)["wtp_statement"] = "Claimed payment evidence."
        signal(self.bundle)["wtp_citations"] = ["src-001"]
        report = self.analyze()
        self.assertIn("UNSUPPORTED_WTP", issue_codes(report, "error"))

    def test_wtp_statement_without_citation_is_rejected(self) -> None:
        signal(self.bundle)["wtp_statement"] = "An uncited WTP statement."
        report = self.analyze()
        self.assertIn("WTP_WITHOUT_CITATION", issue_codes(report, "error"))

    def test_wtp_citation_without_statement_is_rejected(self) -> None:
        signal(self.bundle)["wtp_citations"] = ["src-001"]
        report = self.analyze()
        self.assertIn("WTP_CITATION_WITHOUT_STATEMENT", issue_codes(report, "error"))

    def test_counter_source_cannot_be_used_as_wtp_support(self) -> None:
        source(self.bundle, "src-900")["evidence_types"] = ["observed_payment"]
        signal(self.bundle)["wtp_statement"] = "Improper counter-source WTP statement."
        signal(self.bundle)["wtp_citations"] = ["src-900"]
        report = self.analyze()
        codes = issue_codes(report, "error")
        self.assertIn("WTP_NOT_SUPPORT", codes)
        self.assertIn("UNSUPPORTED_WTP", codes)


class CounterevidenceTests(StudyCase):
    def test_missing_signal_counterquery_is_visible(self) -> None:
        query(self.bundle, "qry-counter")["signal_ids"] = []
        signal(self.bundle)["claimed_level"] = "recurring"
        report = self.analyze()
        self.assertIn("SIGNAL_COUNTERQUERY_MISSING", issue_codes(report, "warning"))
        metric = report["signals"][0]
        self.assertEqual("not_searched", metric["countersearch_status"])
        self.assertEqual("present", metric["counterevidence_level"])

    def test_partial_countersearch_is_not_complete(self) -> None:
        self.bundle["plan"]["counterevidence_status"] = "partial"
        signal(self.bundle)["claimed_level"] = "recurring"
        metric = self.metric()
        self.assertEqual("partial", metric["countersearch_status"])

    def test_complete_countersearch_without_found_counter_is_labeled_narrowly(self) -> None:
        select_signal_sources(self.bundle, [f"src-{index:03d}" for index in range(1, 9)], [])
        metric = self.metric()
        self.assertEqual("complete", metric["countersearch_status"])
        self.assertEqual("none_found_in_coverage", metric["counterevidence_level"])

    def test_cited_counter_source_is_reported_as_present(self) -> None:
        metric = self.metric()
        self.assertEqual(1, metric["counter_sources"])
        self.assertEqual("present", metric["counterevidence_level"])


class QuoteAndLinkIntegrityTests(StudyCase):
    def test_exactly_twenty_five_word_excerpt_is_allowed(self) -> None:
        excerpt = " ".join(f"word{index}" for index in range(25))
        source(self.bundle, "src-001")["captured_text"] = f"prefix {excerpt} suffix"
        source(self.bundle, "src-001")["excerpt"] = excerpt
        report = self.analyze()
        self.assertNotIn("EXCERPT_TOO_LONG", issue_codes(report, "error"))

    def test_twenty_six_word_excerpt_is_rejected(self) -> None:
        excerpt = " ".join(f"word{index}" for index in range(26))
        source(self.bundle, "src-001")["captured_text"] = excerpt
        source(self.bundle, "src-001")["excerpt"] = excerpt
        report = self.analyze()
        self.assertIn("EXCERPT_TOO_LONG", issue_codes(report, "error"))

    def test_nonliteral_quote_is_rejected(self) -> None:
        source(self.bundle, "src-001")["excerpt"] = "A paraphrase that is not in captured text."
        report = self.analyze()
        self.assertIn("QUOTE_MISMATCH", issue_codes(report, "error"))

    def test_query_to_source_link_must_be_bidirectional(self) -> None:
        source(self.bundle, "src-001")["query_ids"] = ["qry-counter"]
        report = self.analyze()
        self.assertIn("QUERY_LINK_MISMATCH", issue_codes(report, "error"))

    def test_query_unknown_source_is_rejected(self) -> None:
        query(self.bundle, "qry-support")["included_source_ids"].append("src-missing")
        report = self.analyze()
        self.assertIn("BAD_SOURCE_REF", issue_codes(report, "error"))

    def test_source_unknown_query_is_rejected(self) -> None:
        source(self.bundle, "src-001")["query_ids"] = ["qry-missing"]
        report = self.analyze()
        self.assertIn("BAD_QUERY_REF", issue_codes(report, "error"))

    def test_source_signal_link_must_be_bidirectional(self) -> None:
        signal(self.bundle)["support_citations"].remove("src-001")
        report = self.analyze()
        self.assertIn("SIGNAL_LINK_MISMATCH", issue_codes(report, "error"))

    def test_signal_citation_stance_must_match(self) -> None:
        source(self.bundle, "src-001")["stance"] = "counter"
        report = self.analyze()
        self.assertIn("STANCE_MISMATCH", issue_codes(report, "error"))

    def test_unknown_signal_in_query_is_rejected(self) -> None:
        query(self.bundle, "qry-counter")["signal_ids"] = ["sig-missing"]
        report = self.analyze()
        self.assertIn("BAD_SIGNAL_REF", issue_codes(report, "error"))

    def test_notes_source_and_signal_references_are_checked(self) -> None:
        self.bundle["notes"]["observations"][0]["source_ids"] = ["src-missing"]
        self.bundle["notes"]["inferences"][0]["signal_ids"] = ["sig-missing"]
        report = self.analyze()
        codes = issue_codes(report, "error")
        self.assertIn("BAD_SOURCE_REF", codes)
        self.assertIn("BAD_SIGNAL_REF", codes)


if __name__ == "__main__":
    unittest.main()
