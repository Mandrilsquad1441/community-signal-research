from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "community_signal.py"

_SPEC = importlib.util.spec_from_file_location("community_signal_under_test", SCRIPT)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - a broken test checkout
    raise RuntimeError(f"Cannot import {SCRIPT}")
csr = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = csr
_SPEC.loader.exec_module(csr)


def clone(value: Any) -> Any:
    return copy.deepcopy(value)


def make_bundle(*, support_count: int = 8, include_counter: bool = True) -> dict[str, Any]:
    """Return a warning-free quick study with a well-corroborated signal."""
    support_ids = [f"src-{index:03d}" for index in range(1, support_count + 1)]
    counter_ids = ["src-900"] if include_counter else []
    plan = {
        "schema_version": "1.0",
        "study_id": "adversarial-study",
        "question": "Which community research failure deserves a portable skill?",
        "decision": "Choose whether to build an auditable community research workflow.",
        "mode": "quick",
        "as_of": "2026-08-30",
        "recency_days": 365,
        "date_window": {"start": "2025-08-30", "end": "2026-08-30"},
        "population": "Coding-agent users discussing research workflows.",
        "scope": {
            "platforms": ["reddit"],
            "communities": ["r/alpha", "r/beta"],
            "languages": ["en"],
        },
        "inclusion_criteria": ["First-person problem or observed behavior."],
        "exclusion_criteria": ["Untraceable summary or undisclosed promotion."],
        "coverage_targets": {
            "source_units": 8,
            "threads": 3,
            "communities": 1,
            "platforms": 1,
            "counter_queries": 1,
        },
        "counterevidence_status": "complete",
        "stop_condition": "Declared coverage reached.",
        "limitations": ["Community search is not a representative survey."],
    }
    queries = [
        {
            "schema_version": "1.0",
            "id": "qry-support",
            "platform": "reddit",
            "query": "research skill recurring failure workaround",
            "intent": "support",
            "run_at": "2026-08-30T09:00:00Z",
            "sort": "relevance and new",
            "results_seen": max(20, support_count),
            "results_screened": support_count,
            "pages_seen": 2,
            "truncated": False,
            "included_source_ids": support_ids,
            "notes": "Included each independently relevant source unit.",
            "signal_ids": ["sig-001"],
        },
        {
            "schema_version": "1.0",
            "id": "qry-counter",
            "platform": "reddit",
            "query": "research workflow existing solution sufficient",
            "intent": "counter",
            "run_at": "2026-08-30T09:30:00Z",
            "sort": "relevance",
            "results_seen": 5,
            "results_screened": 1 if include_counter else 0,
            "pages_seen": 1,
            "truncated": False,
            "included_source_ids": counter_ids,
            "notes": "Counter-oriented search was completed.",
            "signal_ids": ["sig-001"],
        },
    ]
    sources: list[dict[str, Any]] = []
    evidence_cycle = [
        ["problem", "workaround"],
        ["adoption"],
        ["switching_friction"],
        ["urgency"],
    ]
    for offset, source_id in enumerate(support_ids):
        index = offset + 1
        community = "alpha" if offset % 2 == 0 else "beta"
        thread_number = offset % 4 + 1
        captured = (
            f"Independent participant {index} describes research failure {index} and "
            f"an explicit workflow behavior unique to case {index}."
        )
        excerpt = f"Independent participant {index} describes research failure {index}"
        sources.append(
            _public_source(
                source_id=source_id,
                community=community,
                thread_number=thread_number,
                unit=f"c{index:03d}",
                author_number=index,
                captured=captured,
                excerpt=excerpt,
                stance="support",
                evidence_types=evidence_cycle[offset % len(evidence_cycle)],
                query_id="qry-support",
            )
        )
    if include_counter:
        sources.append(
            _public_source(
                source_id="src-900",
                community="alpha",
                thread_number=90,
                unit="counter",
                author_number=900,
                captured="One independent participant says the existing manual research process is sufficient.",
                excerpt="the existing manual research process is sufficient",
                stance="counter",
                evidence_types=["satisfaction"],
                query_id="qry-counter",
            )
        )
    signal = {
        "id": "sig-001",
        "name": "Auditable community research",
        "hypothesis": "Coding-agent users need research that binds claims to community evidence.",
        "decision_relevance": "A portable workflow could prevent unsupported demand claims.",
        "support_citations": support_ids,
        "counter_citations": counter_ids,
        "claimed_level": "well-corroborated" if support_count >= 6 else "anecdotal",
        "wtp_statement": None,
        "wtp_citations": [],
        "alternative_explanations": ["Observed requests may be specific to coding communities."],
        "disconfirming_evidence_needed": "Independent evidence that existing tools are sufficient.",
    }
    notes = {
        "schema_version": "1.0",
        "observations": [
            {
                "text": "Participants describe source-verification failures.",
                "source_ids": [support_ids[0]] if support_ids else counter_ids,
            }
        ],
        "inferences": [
            {
                "text": "Auditability may be a useful product wedge.",
                "signal_ids": ["sig-001"],
            }
        ],
        "recommendation": {
            "text": "Prototype the evidence-bound workflow.",
            "signal_ids": ["sig-001"],
            "caveats": ["Treat the finding as sample-bound."],
        },
        "next_tests": ["Repeat in a non-coding community."],
        "coverage_notes": ["Search ranking was opaque."],
        "stop_reason": "Declared quick-mode coverage was reached.",
    }
    return {
        "plan": plan,
        "queries": queries,
        "sources": sources,
        "catalog": {"schema_version": "1.0", "signals": [signal]},
        "notes": notes,
    }


def _public_source(
    *,
    source_id: str,
    community: str,
    thread_number: int,
    unit: str,
    author_number: int,
    captured: str,
    excerpt: str,
    stance: str,
    evidence_types: list[str],
    query_id: str,
) -> dict[str, Any]:
    thread_slug = f"thread{thread_number}"
    thread_url = f"https://www.reddit.com/r/{community}/comments/{thread_slug}/topic/"
    return {
        "schema_version": "1.0",
        "id": source_id,
        "platform": "reddit",
        "community": f"r/{community}",
        "source_type": "comment",
        "url": thread_url + f"{unit}/",
        "record_ref": None,
        "visibility": "public",
        "capture_method": "browser",
        "source_file_sha256": None,
        "thread_url": thread_url,
        "unit_id": f"reddit:t1_{unit}",
        "thread_id": f"reddit:t3_{thread_slug}",
        "published_at": f"2026-0{(thread_number % 7) + 1}-15T10:00:00Z",
        "collected_at": "2026-08-30T10:00:00Z",
        "author_key": f"author:{author_number:016x}",
        "language": "en",
        "source_status": "available",
        "title": "Community research workflow",
        "captured_text": captured,
        "excerpt": excerpt,
        "stance": stance,
        "evidence_types": evidence_types,
        "promotional": "no",
        "repost_of": None,
        "query_ids": [query_id],
        "signal_ids": ["sig-001"],
        "engagement": {
            "score": 1,
            "comments": 1,
            "snapshot_at": "2026-08-30T10:00:00Z",
        },
        "duplicate_reviews": [],
        "notes": "",
    }


def signal(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle["catalog"]["signals"][0]


def source(bundle: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(item for item in bundle["sources"] if item["id"] == source_id)


def set_reddit_thread(item: dict[str, Any], post_id: str) -> None:
    """Move one synthetic Reddit fixture to a different native thread coherently."""
    community = str(item["community"]).split("/", 1)[-1]
    thread_url = f"https://www.reddit.com/r/{community}/comments/{post_id}/topic/"
    item["thread_url"] = thread_url
    item["thread_id"] = f"reddit:t3_{post_id.casefold()}"
    if item.get("source_type") == "post":
        item["url"] = thread_url
        item["unit_id"] = item["thread_id"]
    else:
        unit_id = str(item["unit_id"]).split("_", 1)[-1]
        item["url"] = thread_url + unit_id + "/"


def query(bundle: dict[str, Any], query_id: str) -> dict[str, Any]:
    return next(item for item in bundle["queries"] if item["id"] == query_id)


def select_signal_sources(
    bundle: dict[str, Any], support_ids: list[str], counter_ids: list[str] | None = None
) -> None:
    counter_ids = [] if counter_ids is None else counter_ids
    sig = signal(bundle)
    sig["support_citations"] = list(support_ids)
    sig["counter_citations"] = list(counter_ids)
    selected = set(support_ids) | set(counter_ids)
    for item in bundle["sources"]:
        item["signal_ids"] = ["sig-001"] if item["id"] in selected else []


def write_bundle(study_dir: Path, bundle: dict[str, Any]) -> None:
    study_dir.mkdir(parents=True, exist_ok=True)
    (study_dir / "study-plan.json").write_text(
        json.dumps(bundle["plan"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (study_dir / "query-log.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in bundle["queries"]),
        encoding="utf-8",
    )
    (study_dir / "source-ledger.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in bundle["sources"]),
        encoding="utf-8",
    )
    (study_dir / "signal-catalog.json").write_text(
        json.dumps(bundle["catalog"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (study_dir / "research-notes.json").write_text(
        json.dumps(bundle["notes"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def read_bundle(study_dir: Path) -> dict[str, Any]:
    return {
        "plan": json.loads((study_dir / "study-plan.json").read_text(encoding="utf-8")),
        "queries": [json.loads(line) for line in (study_dir / "query-log.jsonl").read_text(encoding="utf-8").splitlines() if line],
        "sources": [json.loads(line) for line in (study_dir / "source-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line],
        "catalog": json.loads((study_dir / "signal-catalog.json").read_text(encoding="utf-8")),
        "notes": json.loads((study_dir / "research-notes.json").read_text(encoding="utf-8")),
    }


def analyze_bundle(study_dir: Path, bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    write_bundle(study_dir, bundle)
    return csr.analyze(study_dir)


def issue_codes(report: dict[str, Any], severity: str | None = None) -> set[str]:
    return {
        item["code"]
        for item in report["issues"]
        if severity is None or item["severity"] == severity
    }


def artifact_bytes(study_dir: Path) -> dict[str, bytes]:
    artifacts = study_dir / "artifacts"
    return {name: (artifacts / name).read_bytes() for name in ("signals.csv", "findings.md", "audit.json")}


def run_cli(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def sha256_prefixed(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
