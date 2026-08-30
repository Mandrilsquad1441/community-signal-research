#!/usr/bin/env python3
"""Deterministic validator and report builder for community-signal-research.

The program is intentionally standard-library-only and performs no network calls.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import html
import ipaddress
import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
INPUT_FILES = (
    "study-plan.json",
    "query-log.jsonl",
    "source-ledger.jsonl",
    "signal-catalog.json",
    "research-notes.json",
)
ARTIFACT_NAMES = ("signals.csv", "findings.md", "audit.json")
LEVELS = ("unsupported", "anecdotal", "recurring", "well-corroborated")
LEVEL_RANK = {name: index for index, name in enumerate(LEVELS)}
MODES = {"quick", "standard"}
COUNTER_STATUSES = {"planned", "partial", "complete"}
QUERY_INTENTS = {"support", "counter", "neutral"}
SOURCE_TYPES = {
    "post",
    "comment",
    "issue",
    "discussion",
    "story",
    "review",
    "export_record",
    "other",
}
STANCES = {"support", "counter", "neutral"}
EVIDENCE_TYPES = {
    "problem",
    "desired_outcome",
    "workaround",
    "urgency",
    "switching_friction",
    "adoption",
    "purchase_intent",
    "observed_payment",
    "constraint",
    "satisfaction",
}
COSTLY_BEHAVIOR_TYPES = {
    "workaround",
    "switching_friction",
    "adoption",
    "observed_payment",
}
RANKED_EVIDENCE_TYPES = COSTLY_BEHAVIOR_TYPES | {"problem", "urgency", "purchase_intent"}
PROMOTIONAL_VALUES = {"yes", "no", "unclear"}
VISIBILITY_VALUES = {"public", "supplied_private"}
CAPTURE_METHODS = {"browser", "web_search", "api", "connector", "manual", "export"}
SOURCE_STATUSES = {"available", "edited", "deleted", "unavailable", "unknown"}
AUTHOR_KEY_RE = re.compile(r"^author:[0-9a-f]{16,64}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")
STUDY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
OPAQUE_RECORD_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
DISALLOWED_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "ref_source",
    "share_id",
    "context",
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_CAPTURED_TEXT = 100_000
MAX_GENERAL_STRING = 20_000
MAX_EXCERPT_CHARS = 500
MAX_FUZZY_PAIRS = 100_000
MODE_FLOORS = {
    "quick": {"source_units": 8, "threads": 3, "communities": 1, "platforms": 1, "counter_queries": 1},
    "standard": {"source_units": 25, "threads": 8, "communities": 3, "platforms": 2, "counter_queries": 2},
}

PLAN_REQUIRED = {
    "schema_version",
    "study_id",
    "question",
    "decision",
    "mode",
    "as_of",
    "recency_days",
    "date_window",
    "population",
    "scope",
    "inclusion_criteria",
    "exclusion_criteria",
    "coverage_targets",
    "counterevidence_status",
    "stop_condition",
    "limitations",
}
QUERY_REQUIRED = {
    "schema_version",
    "id",
    "platform",
    "query",
    "intent",
    "run_at",
    "sort",
    "results_seen",
    "results_screened",
    "pages_seen",
    "truncated",
    "included_source_ids",
    "notes",
    "signal_ids",
}
SOURCE_REQUIRED = {
    "schema_version",
    "id",
    "platform",
    "community",
    "source_type",
    "url",
    "record_ref",
    "visibility",
    "capture_method",
    "source_file_sha256",
    "thread_url",
    "unit_id",
    "thread_id",
    "published_at",
    "collected_at",
    "author_key",
    "language",
    "source_status",
    "title",
    "captured_text",
    "excerpt",
    "stance",
    "evidence_types",
    "promotional",
    "repost_of",
    "query_ids",
    "signal_ids",
    "engagement",
    "duplicate_reviews",
    "notes",
}
SIGNAL_REQUIRED = {
    "id",
    "name",
    "hypothesis",
    "decision_relevance",
    "support_citations",
    "counter_citations",
    "claimed_level",
    "wtp_statement",
    "wtp_citations",
    "alternative_explanations",
    "disconfirming_evidence_needed",
}
NOTES_REQUIRED = {
    "schema_version",
    "observations",
    "inferences",
    "recommendation",
    "next_tests",
    "coverage_notes",
    "stop_reason",
}


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    path: str
    message: str


class Audit:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("error", code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("warning", code, path, message))

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


class DuplicateKeyError(ValueError):
    pass


class UnionFind:
    def __init__(self, keys: Iterable[str]) -> None:
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            self.parent[root_right] = root_left
        else:
            self.parent[root_left] = root_right


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def strict_json_loads(value: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if DISALLOWED_CONTROL_RE.search(key):
                raise ValueError("JSON object key contains a disallowed control character")
            if key in result:
                raise DuplicateKeyError(f"duplicate object key {key!r}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> Any:
        raise ValueError(f"non-finite JSON number {constant!r} is not allowed")

    return json.loads(value, object_pairs_hook=object_pairs, parse_constant=reject_constant)


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def word_count(value: str) -> int:
    return len(re.findall(r"\S+", normalize_text(value)))


def parse_datetime(value: Any, path: str, audit: Audit) -> datetime | None:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected an ISO 8601 timestamp string.")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        audit.error("TIMESTAMP", path, f"Invalid ISO 8601 timestamp: {value!r}.")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        audit.error("TIMEZONE", path, "Timestamp must include a timezone or Z suffix.")
        return None
    return parsed.astimezone(timezone.utc)


def parse_date(value: Any, path: str, audit: Audit) -> date | None:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected an ISO 8601 date string.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        audit.error("DATE", path, f"Invalid ISO 8601 date: {value!r}.")
        return None


def canonicalize_url(value: str) -> str:
    if DISALLOWED_CONTROL_RE.search(value) or "\r" in value or "\n" in value or "\t" in value:
        raise ValueError("URL contains control characters")
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise ValueError("URL requires a scheme and host")
    if scheme not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL credentials are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    host_for_netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host_for_netloc}:{port}"
    else:
        netloc = host_for_netloc
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    fragment = ""
    query_pairs = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS]

    if host in {"reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "www.reddit.com"}:
        scheme = "https"
        netloc = "www.reddit.com"
        query_pairs = []
        path = re.sub(r"^/[a-z]{2}(?:-[A-Z]{2})?(?=/r/)", "", path)
        path = path.rstrip("/") + "/"
    elif host == "github.com" or host.endswith(".github.com"):
        scheme = "https"
        netloc = host
        query_pairs = [(key, val) for key, val in query_pairs if key.lower() not in {"notification_referrer_id"}]
        if re.fullmatch(r"(?:issuecomment|discussioncomment|discussion_r)-\d+", parts.fragment or ""):
            fragment = parts.fragment
        path = path.rstrip("/") or "/"
    elif host in {"news.ycombinator.com", "www.news.ycombinator.com"}:
        scheme = "https"
        netloc = "news.ycombinator.com"
        if path.rstrip("/") == "/item":
            item_ids = [(key, val) for key, val in query_pairs if key == "id" and val.isdigit()]
            if not item_ids:
                raise ValueError("Hacker News item URL requires a numeric id")
            query_pairs = [item_ids[0]]
            path = "/item"
        else:
            query_pairs = []
            path = path.rstrip("/") or "/"
    else:
        path = path.rstrip("/") or "/"
        query_pairs.sort()
        fragment = parts.fragment

    return urlunsplit((scheme, netloc, path, urlencode(query_pairs, doseq=True), fragment))


def derived_community(platform: str, url: str) -> str | None:
    try:
        canonical = canonicalize_url(url)
    except ValueError:
        return None
    parts = urlsplit(canonical)
    path_parts = [part for part in parts.path.split("/") if part]
    platform = platform.casefold()
    if platform == "reddit" and len(path_parts) >= 2 and path_parts[0].casefold() == "r":
        return "r/" + path_parts[1]
    if platform == "github" and len(path_parts) >= 2 and parts.hostname == "github.com":
        return path_parts[0] + "/" + path_parts[1]
    if platform in {"hackernews", "hn"} and parts.hostname == "news.ycombinator.com":
        return "news.ycombinator.com"
    return None


def check_object(
    value: Any,
    path: str,
    required: set[str],
    audit: Audit,
    allowed: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        audit.error("TYPE", path, "Expected an object.")
        return {}
    allowed = required if allowed is None else allowed
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)
    for key in missing:
        audit.error("MISSING_FIELD", f"{path}.{key}", "Required field is missing.")
    for key in unknown:
        audit.error("UNKNOWN_FIELD", f"{path}.{key}", "Unknown field; fix misspellings or remove it.")
    return value


def check_string(value: Any, path: str, audit: Audit, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected a string.")
        return ""
    if not allow_empty and not value.strip():
        audit.error("EMPTY_STRING", path, "Value must not be empty.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        audit.error("UNICODE", path, "String contains an invalid Unicode surrogate.")
    if DISALLOWED_CONTROL_RE.search(value):
        audit.error("CONTROL_CHARACTER", path, "String contains a disallowed C0 or C1 control character.")
    if len(value) > MAX_GENERAL_STRING:
        audit.error("STRING_TOO_LARGE", path, f"String exceeds {MAX_GENERAL_STRING} characters.")
    return value


def check_string_list(value: Any, path: str, audit: Audit, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        audit.error("TYPE", path, "Expected a list of strings.")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            audit.error("TYPE", f"{path}[{index}]", "Expected a non-empty string.")
        else:
            if DISALLOWED_CONTROL_RE.search(item):
                audit.error("CONTROL_CHARACTER", f"{path}[{index}]", "String contains a disallowed C0 or C1 control character.")
            result.append(item)
    if not allow_empty and not result:
        audit.error("EMPTY_LIST", path, "At least one value is required.")
    if len(result) != len(set(result)):
        audit.error("DUPLICATE_LIST_VALUE", path, "List values must be unique.")
    return result


def check_nonnegative_int(value: Any, path: str, audit: Audit) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        audit.error("TYPE", path, "Expected a non-negative integer.")
        return 0
    return value


def read_input_text(path: Path, audit: Audit) -> str | None:
    root = path.parent.resolve()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        audit.error("MISSING_FILE", path.name, "Required input file is missing.")
        return None
    if path.is_symlink() or not resolved.is_relative_to(root):
        audit.error("UNSAFE_INPUT_PATH", path.name, "Input must be a regular file inside the study directory, not a symlink or junction escape.")
        return None
    if not resolved.is_file():
        audit.error("MISSING_FILE", path.name, "Required input is not a regular file.")
        return None
    if resolved.stat().st_size > MAX_FILE_BYTES:
        audit.error("FILE_TOO_LARGE", path.name, f"Input exceeds {MAX_FILE_BYTES} bytes.")
        return None
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        audit.error("ENCODING", path.name, "File must be UTF-8 encoded.")
        return None


def load_json(path: Path, audit: Audit) -> Any:
    text = read_input_text(path, audit)
    if text is None:
        return {}
    try:
        return strict_json_loads(text)
    except json.JSONDecodeError as exc:
        audit.error("JSON", path.name, f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}.")
    except (DuplicateKeyError, ValueError) as exc:
        audit.error("JSON", path.name, str(exc) + ".")
    return {}


def load_jsonl(path: Path, audit: Audit) -> list[Any]:
    text = read_input_text(path, audit)
    if text is None:
        return []
    lines = text.splitlines()
    records: list[Any] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(strict_json_loads(line))
        except json.JSONDecodeError as exc:
            audit.error("JSONL", f"{path.name}:{line_number}", f"Invalid JSON: {exc.msg}.")
        except (DuplicateKeyError, ValueError) as exc:
            audit.error("JSONL", f"{path.name}:{line_number}", str(exc) + ".")
        if len(records) > MAX_RECORDS:
            audit.error("TOO_MANY_RECORDS", path.name, f"Input exceeds {MAX_RECORDS} records.")
            break
    return records


def validate_schema_version(record: dict[str, Any], path: str, audit: Audit) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        audit.error("SCHEMA_VERSION", f"{path}.schema_version", f"Expected {SCHEMA_VERSION!r}.")


def validate_plan(raw: Any, audit: Audit) -> dict[str, Any]:
    plan = check_object(raw, "study-plan.json", PLAN_REQUIRED, audit)
    if not plan:
        return {}
    validate_schema_version(plan, "study-plan.json", audit)
    study_id = check_string(plan.get("study_id"), "study-plan.json.study_id", audit)
    if study_id and not STUDY_ID_RE.fullmatch(study_id):
        audit.error("STUDY_ID", "study-plan.json.study_id", "Use 3-80 lowercase letters, digits, dots, underscores, or hyphens.")
    for key in ("question", "decision", "population", "stop_condition"):
        check_string(plan.get(key), f"study-plan.json.{key}", audit)
    if plan.get("mode") not in MODES:
        audit.error("ENUM", "study-plan.json.mode", f"Expected one of {sorted(MODES)}.")
    as_of = parse_date(plan.get("as_of"), "study-plan.json.as_of", audit)
    recency_days = check_nonnegative_int(plan.get("recency_days"), "study-plan.json.recency_days", audit)
    if recency_days == 0:
        audit.error("RANGE", "study-plan.json.recency_days", "Must be greater than zero.")
    elif as_of and recency_days > (as_of - date.min).days:
        audit.error(
            "RANGE",
            "study-plan.json.recency_days",
            f"Must not reach before {date.min.isoformat()} for as_of {as_of.isoformat()}.",
        )

    date_window = check_object(plan.get("date_window"), "study-plan.json.date_window", {"start", "end"}, audit)
    start_date = parse_date(date_window.get("start"), "study-plan.json.date_window.start", audit)
    end_date = parse_date(date_window.get("end"), "study-plan.json.date_window.end", audit)
    if start_date and end_date and start_date > end_date:
        audit.error("DATE_WINDOW", "study-plan.json.date_window", "Start date cannot be later than end date.")
    if end_date and as_of and end_date > as_of:
        audit.error("DATE_WINDOW", "study-plan.json.date_window.end", "Date-window end cannot be later than as_of.")

    scope = check_object(plan.get("scope"), "study-plan.json.scope", {"platforms", "communities", "languages"}, audit)
    for key in ("platforms", "communities", "languages"):
        values = check_string_list(scope.get(key), f"study-plan.json.scope.{key}", audit, allow_empty=False)
        if len({value.casefold() for value in values}) != len(values):
            audit.error("DUPLICATE_NORMALIZED_VALUE", f"study-plan.json.scope.{key}", "Values must also be unique when case-folded.")
    check_string_list(plan.get("inclusion_criteria"), "study-plan.json.inclusion_criteria", audit, allow_empty=False)
    check_string_list(plan.get("exclusion_criteria"), "study-plan.json.exclusion_criteria", audit, allow_empty=False)
    check_string_list(plan.get("limitations"), "study-plan.json.limitations", audit)

    targets = check_object(
        plan.get("coverage_targets"),
        "study-plan.json.coverage_targets",
        {"source_units", "threads", "communities", "platforms", "counter_queries"},
        audit,
    )
    for key in ("source_units", "threads", "communities", "platforms", "counter_queries"):
        target = check_nonnegative_int(targets.get(key), f"study-plan.json.coverage_targets.{key}", audit)
        if target == 0:
            audit.error("ZERO_COVERAGE_TARGET", f"study-plan.json.coverage_targets.{key}", "Coverage targets must be positive.")
        floor = MODE_FLOORS.get(plan.get("mode"), {}).get(key, 0)
        if target < floor:
            audit.warn("TARGET_BELOW_MODE_FLOOR", f"study-plan.json.coverage_targets.{key}", f"Mode {plan.get('mode')!r} uses an effective minimum of {floor}.")
    if plan.get("counterevidence_status") not in COUNTER_STATUSES:
        audit.error("ENUM", "study-plan.json.counterevidence_status", f"Expected one of {sorted(COUNTER_STATUSES)}.")
    return plan


def validate_queries(
    raw_records: list[Any], plan: dict[str, Any], audit: Audit
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    scope = plan.get("scope", {}) if isinstance(plan.get("scope"), dict) else {}
    allowed_platforms = {str(value).casefold() for value in scope.get("platforms", [])}
    for index, raw in enumerate(raw_records):
        path = f"query-log.jsonl[{index}]"
        record = check_object(raw, path, QUERY_REQUIRED, audit)
        if not record:
            continue
        validate_schema_version(record, path, audit)
        query_id = check_string(record.get("id"), f"{path}.id", audit)
        if query_id and not ID_RE.fullmatch(query_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif query_id and not re.fullmatch(r"qry[-_][a-z0-9][a-z0-9_-]*", query_id):
            audit.error("ID_PREFIX", f"{path}.id", "Query IDs must start with qry- or qry_.")
        if query_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate query ID {query_id!r}.")
        platform = check_string(record.get("platform"), f"{path}.platform", audit)
        if allowed_platforms and platform.casefold() not in allowed_platforms:
            audit.error("PLATFORM_SCOPE", f"{path}.platform", "Query platform is outside the declared scope.")
        check_string(record.get("query"), f"{path}.query", audit)
        if record.get("intent") not in QUERY_INTENTS:
            audit.error("ENUM", f"{path}.intent", f"Expected one of {sorted(QUERY_INTENTS)}.")
        parse_datetime(record.get("run_at"), f"{path}.run_at", audit)
        check_string(record.get("sort"), f"{path}.sort", audit)
        seen = check_nonnegative_int(record.get("results_seen"), f"{path}.results_seen", audit)
        screened = check_nonnegative_int(record.get("results_screened"), f"{path}.results_screened", audit)
        check_nonnegative_int(record.get("pages_seen"), f"{path}.pages_seen", audit)
        if seen > 0 and isinstance(record.get("pages_seen"), int) and record.get("pages_seen") < 1:
            audit.error("PAGES_SEEN", f"{path}.pages_seen", "At least one page must be recorded when results were seen.")
        if screened > seen:
            audit.error("SCREENED_GT_SEEN", f"{path}.results_screened", "Screened results cannot exceed seen results.")
        if not isinstance(record.get("truncated"), bool):
            audit.error("TYPE", f"{path}.truncated", "Expected a boolean.")
        included_source_ids = check_string_list(record.get("included_source_ids"), f"{path}.included_source_ids", audit)
        if included_source_ids and (seen == 0 or screened == 0):
            audit.error(
                "QUERY_COUNT_MISMATCH",
                f"{path}.included_source_ids",
                "A query with included sources must report at least one seen and screened result.",
            )
        check_string_list(record.get("signal_ids"), f"{path}.signal_ids", audit)
        check_string(record.get("notes"), f"{path}.notes", audit, allow_empty=True)
        records.append(record)
        if query_id and query_id not in by_id:
            by_id[query_id] = record
    return records, by_id


def validate_sources(
    raw_records: list[Any], plan: dict[str, Any], audit: Audit
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    as_of = parse_date(plan.get("as_of"), "study-plan.json.as_of", Audit()) if plan else None
    date_window = plan.get("date_window", {}) if isinstance(plan.get("date_window"), dict) else {}
    start_date = parse_date(date_window.get("start"), "study-plan.json.date_window.start", Audit()) if date_window else None
    end_date = parse_date(date_window.get("end"), "study-plan.json.date_window.end", Audit()) if date_window else None
    scope = plan.get("scope", {}) if isinstance(plan.get("scope"), dict) else {}
    allowed_platforms = {str(value).casefold() for value in scope.get("platforms", [])}
    allowed_communities = {str(value).casefold() for value in scope.get("communities", [])}
    allowed_languages = {str(value).casefold() for value in scope.get("languages", [])}
    for index, raw in enumerate(raw_records):
        path = f"source-ledger.jsonl[{index}]"
        record = check_object(raw, path, SOURCE_REQUIRED, audit)
        if not record:
            continue
        validate_schema_version(record, path, audit)
        source_id = check_string(record.get("id"), f"{path}.id", audit)
        if source_id and not ID_RE.fullmatch(source_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif source_id and not re.fullmatch(r"src[-_][a-z0-9][a-z0-9_-]*", source_id):
            audit.error("ID_PREFIX", f"{path}.id", "Source IDs must start with src- or src_.")
        if source_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate source ID {source_id!r}.")
        for key in ("platform", "community", "unit_id", "thread_id", "language", "excerpt"):
            check_string(record.get(key), f"{path}.{key}", audit)
        check_string(record.get("title"), f"{path}.title", audit, allow_empty=True)
        platform = str(record.get("platform", "")).casefold()
        community = str(record.get("community", "")).casefold()
        if allowed_platforms and platform not in allowed_platforms:
            audit.error("PLATFORM_SCOPE", f"{path}.platform", "Source platform is outside the declared scope.")
        if allowed_communities and community not in allowed_communities:
            audit.error("COMMUNITY_SCOPE", f"{path}.community", "Source community is outside the declared scope.")
        native_prefixes = {platform + ":"}
        if platform in {"hackernews", "hn"}:
            native_prefixes = {"hackernews:", "hn:"}
        for key in ("unit_id", "thread_id"):
            value = str(record.get(key, "")).casefold()
            if value and not any(value.startswith(prefix) for prefix in native_prefixes):
                audit.warn("NATIVE_ID_PREFIX", f"{path}.{key}", f"Use a stable native ID prefixed for platform {platform!r}.")
        visibility = record.get("visibility")
        if visibility not in VISIBILITY_VALUES:
            audit.error("ENUM", f"{path}.visibility", f"Expected one of {sorted(VISIBILITY_VALUES)}.")
        if record.get("capture_method") not in CAPTURE_METHODS:
            audit.error("ENUM", f"{path}.capture_method", f"Expected one of {sorted(CAPTURE_METHODS)}.")
        if record.get("source_status") not in SOURCE_STATUSES:
            audit.error("ENUM", f"{path}.source_status", f"Expected one of {sorted(SOURCE_STATUSES)}.")
        if record.get("source_type") not in SOURCE_TYPES:
            audit.error("ENUM", f"{path}.source_type", f"Expected one of {sorted(SOURCE_TYPES)}.")
        url = record.get("url")
        thread_url = record.get("thread_url")
        record_ref = record.get("record_ref")
        file_hash = record.get("source_file_sha256")
        if visibility == "public":
            if record_ref is not None:
                audit.error("PUBLIC_RECORD_REF", f"{path}.record_ref", "Public sources use a URL and must set record_ref to null.")
            if file_hash is not None:
                audit.error("PUBLIC_FILE_HASH", f"{path}.source_file_sha256", "Public sources must set source_file_sha256 to null.")
            canonical_urls: dict[str, str] = {}
            for key, value in (("url", url), ("thread_url", thread_url)):
                value = check_string(value, f"{path}.{key}", audit)
                if value:
                    try:
                        canonical = canonicalize_url(value)
                        canonical_urls[key] = canonical
                        if value != canonical:
                            audit.warn("NONCANONICAL_URL", f"{path}.{key}", f"Use canonical URL {canonical!r}.")
                    except ValueError as exc:
                        audit.error("URL", f"{path}.{key}", str(exc) + ".")
            canonical_url = canonical_urls.get("url", "")
            canonical_thread_url = canonical_urls.get("thread_url", "")
            host = (urlsplit(canonical_url).hostname or "").casefold() if canonical_url else ""
            if host:
                unsafe_host = (
                    host == "localhost"
                    or host.endswith((".localhost", ".local", ".internal", ".lan"))
                    or "." not in host
                )
                try:
                    address = ipaddress.ip_address(host.split("%", 1)[0])
                except ValueError:
                    address = None
                if address is not None and (
                    address.is_private
                    or address.is_loopback
                    or address.is_link_local
                    or address.is_reserved
                    or address.is_unspecified
                ):
                    unsafe_host = True
                if unsafe_host:
                    audit.error(
                        "NONPUBLIC_URL",
                        f"{path}.url",
                        "Public evidence URLs must use a publicly routable host, not localhost, a private/link-local address, or a single-label intranet host.",
                    )
            expected_hosts = {
                "reddit": {"www.reddit.com"},
                "github": {"github.com"},
                "hackernews": {"news.ycombinator.com"},
                "hn": {"news.ycombinator.com"},
            }
            if platform in expected_hosts and host not in expected_hosts[platform]:
                audit.error(
                    "PLATFORM_URL_MISMATCH",
                    f"{path}.url",
                    f"Platform {platform!r} requires a URL on {sorted(expected_hosts[platform])!r}.",
                )
            derived = derived_community(platform, str(url or ""))
            if derived and derived.casefold() != str(record.get("community", "")).casefold():
                audit.error("COMMUNITY_MISMATCH", f"{path}.community", f"Canonical URL implies community {derived!r}.")
            if platform == "reddit" and host == "www.reddit.com":
                url_parts = [part for part in urlsplit(canonical_url).path.split("/") if part]
                thread_parts = [part for part in urlsplit(canonical_thread_url).path.split("/") if part]
                valid_url_thread = len(url_parts) >= 4 and url_parts[0].casefold() == "r" and url_parts[2].casefold() == "comments"
                valid_thread_url = (
                    4 <= len(thread_parts) <= 5
                    and thread_parts[0].casefold() == "r"
                    and thread_parts[2].casefold() == "comments"
                )
                if not valid_thread_url:
                    audit.error("THREAD_PERMALINK", f"{path}.thread_url", "Reddit thread_url must be a direct post permalink, not a comment permalink.")
                if valid_url_thread and valid_thread_url:
                    url_post_id = url_parts[3].casefold()
                    thread_post_id = thread_parts[3].casefold()
                    if url_post_id != thread_post_id:
                        audit.error("THREAD_URL_MISMATCH", f"{path}.thread_url", "Source URL and thread_url refer to different Reddit posts.")
                    expected_thread_id = f"reddit:t3_{url_post_id}"
                    if str(record.get("thread_id", "")).casefold() != expected_thread_id:
                        audit.error("NATIVE_ID_MISMATCH", f"{path}.thread_id", f"Reddit URL requires thread_id {expected_thread_id!r}.")
                    if record.get("source_type") == "post":
                        if str(record.get("unit_id", "")).casefold() != expected_thread_id:
                            audit.error("NATIVE_ID_MISMATCH", f"{path}.unit_id", f"Reddit post URL requires unit_id {expected_thread_id!r}.")
                    elif record.get("source_type") == "comment":
                        if len(url_parts) < 6:
                            audit.error("COMMENT_PERMALINK", f"{path}.url", "Reddit comment sources require a direct comment permalink, not only the thread URL.")
                        else:
                            expected_unit_id = f"reddit:t1_{url_parts[5].casefold()}"
                            if str(record.get("unit_id", "")).casefold() != expected_unit_id:
                                audit.error("NATIVE_ID_MISMATCH", f"{path}.unit_id", f"Reddit comment URL requires unit_id {expected_unit_id!r}.")
                elif record.get("source_type") == "comment":
                    audit.error("COMMENT_PERMALINK", f"{path}.url", "Reddit comment sources require a direct comment permalink, not only the thread URL.")
            if platform == "github" and record.get("source_type") == "comment":
                try:
                    fragment = urlsplit(canonical_url).fragment
                except ValueError:
                    fragment = ""
                if not re.fullmatch(r"(?:issuecomment|discussioncomment|discussion_r)-\d+", fragment):
                    audit.error("COMMENT_PERMALINK", f"{path}.url", "GitHub comment sources require an issue or discussion comment fragment.")
        elif visibility == "supplied_private":
            if url is not None or thread_url is not None:
                audit.error("PRIVATE_URL", f"{path}.url", "Supplied-private records must set url and thread_url to null.")
            record_ref = check_string(record_ref, f"{path}.record_ref", audit)
            if record_ref and not OPAQUE_RECORD_REF_RE.fullmatch(record_ref):
                audit.error(
                    "RECORD_REF",
                    f"{path}.record_ref",
                    "Use an opaque 1-256 character reference with letters, digits, dots, underscores, colons, slashes, hashes, or hyphens; do not include personal data.",
                )
            if EMAIL_RE.search(record_ref) or PHONE_RE.search(record_ref):
                audit.error("RECORD_REF_PII", f"{path}.record_ref", "Opaque private provenance must not contain an email address or phone number.")
            if not isinstance(file_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", file_hash):
                audit.error("PROVENANCE_HASH", f"{path}.source_file_sha256", "Supplied-private records require sha256:<64 lowercase hex> provenance.")
            if record.get("source_type") != "export_record" or record.get("capture_method") != "export":
                audit.error("EXPORT_CONTRACT", path, "Supplied-private sources require source_type 'export_record' and capture_method 'export'.")
        published = parse_datetime(record.get("published_at"), f"{path}.published_at", audit)
        collected = parse_datetime(record.get("collected_at"), f"{path}.collected_at", audit)
        if published and collected and published > collected:
            audit.error("TIME_ORDER", f"{path}.published_at", "Publication cannot be later than collection.")
        if published and as_of and published.date() > as_of:
            audit.error("AFTER_AS_OF", f"{path}.published_at", "Publication is later than the study as_of date.")
        if published and start_date and published.date() < start_date:
            audit.error("OUTSIDE_DATE_WINDOW", f"{path}.published_at", "Publication is before the declared date-window start.")
        if published and end_date and published.date() > end_date:
            audit.error("OUTSIDE_DATE_WINDOW", f"{path}.published_at", "Publication is after the declared date-window end.")
        language = str(record.get("language", "")).casefold()
        if allowed_languages and language not in allowed_languages:
            audit.error("LANGUAGE_SCOPE", f"{path}.language", "Source language is outside the declared scope.")
        author_key = check_string(record.get("author_key"), f"{path}.author_key", audit)
        if author_key != "unknown" and not AUTHOR_KEY_RE.fullmatch(author_key):
            audit.error("AUTHOR_PRIVACY", f"{path}.author_key", "Use 'unknown' or an opaque author: key with 16-64 lowercase hex characters.")
        captured = record.get("captured_text") if isinstance(record.get("captured_text"), str) else ""
        excerpt = record.get("excerpt") if isinstance(record.get("excerpt"), str) else ""
        if not isinstance(record.get("captured_text"), str):
            audit.error("TYPE", f"{path}.captured_text", "Expected a string.")
        elif not captured.strip():
            audit.error("EMPTY_STRING", f"{path}.captured_text", "Value must not be empty.")
        else:
            try:
                captured.encode("utf-8")
            except UnicodeEncodeError:
                audit.error("UNICODE", f"{path}.captured_text", "String contains an invalid Unicode surrogate.")
            if DISALLOWED_CONTROL_RE.search(captured):
                audit.error("CONTROL_CHARACTER", f"{path}.captured_text", "Captured text contains a disallowed C0 or C1 control character.")
        if len(captured) > MAX_CAPTURED_TEXT:
            audit.error("STRING_TOO_LARGE", f"{path}.captured_text", f"Captured text exceeds {MAX_CAPTURED_TEXT} characters.")
        if captured and excerpt and excerpt not in captured:
            audit.error("QUOTE_MISMATCH", f"{path}.excerpt", "Excerpt is not a literal substring of captured_text.")
        if excerpt and word_count(excerpt) > 25:
            audit.error("EXCERPT_TOO_LONG", f"{path}.excerpt", "Public excerpt exceeds the 25-word limit.")
        if len(excerpt) > MAX_EXCERPT_CHARS:
            audit.error("EXCERPT_TOO_LARGE", f"{path}.excerpt", f"Excerpt exceeds {MAX_EXCERPT_CHARS} characters.")
        if EMAIL_RE.search(captured) or PHONE_RE.search(captured):
            audit.warn("POSSIBLE_PII", f"{path}.captured_text", "Review and redact incidental email addresses or phone numbers.")
        if record.get("stance") not in STANCES:
            audit.error("ENUM", f"{path}.stance", f"Expected one of {sorted(STANCES)}.")
        evidence_types = check_string_list(record.get("evidence_types"), f"{path}.evidence_types", audit, allow_empty=False)
        for evidence_type in evidence_types:
            if evidence_type not in EVIDENCE_TYPES:
                audit.error("ENUM", f"{path}.evidence_types", f"Unknown evidence type {evidence_type!r}.")
        if record.get("promotional") not in PROMOTIONAL_VALUES:
            audit.error("ENUM", f"{path}.promotional", f"Expected one of {sorted(PROMOTIONAL_VALUES)}.")
        repost_of = record.get("repost_of")
        if repost_of is not None and (not isinstance(repost_of, str) or not repost_of):
            audit.error("TYPE", f"{path}.repost_of", "Expected null or a source ID string.")
        check_string_list(record.get("query_ids"), f"{path}.query_ids", audit, allow_empty=False)
        check_string_list(record.get("signal_ids"), f"{path}.signal_ids", audit)
        engagement = check_object(
            record.get("engagement"),
            f"{path}.engagement",
            {"score", "comments", "snapshot_at"},
            audit,
        )
        score_value = engagement.get("score")
        if score_value is not None and (isinstance(score_value, bool) or not isinstance(score_value, int)):
            audit.error("TYPE", f"{path}.engagement.score", "Expected an integer or null; negative scores are valid.")
        comments_value = engagement.get("comments")
        if comments_value is not None:
            check_nonnegative_int(comments_value, f"{path}.engagement.comments", audit)
        snapshot = parse_datetime(engagement.get("snapshot_at"), f"{path}.engagement.snapshot_at", audit)
        if published and snapshot and snapshot < published:
            audit.error("TIME_ORDER", f"{path}.engagement.snapshot_at", "Engagement snapshot cannot predate publication.")
        reviews = record.get("duplicate_reviews")
        if not isinstance(reviews, list):
            audit.error("TYPE", f"{path}.duplicate_reviews", "Expected a list.")
        else:
            seen_review_ids: set[str] = set()
            for review_index, raw_review in enumerate(reviews):
                review_path = f"{path}.duplicate_reviews[{review_index}]"
                review = check_object(raw_review, review_path, {"other_source_id", "decision", "reason"}, audit)
                other_id = check_string(review.get("other_source_id"), f"{review_path}.other_source_id", audit)
                if other_id == source_id:
                    audit.error("SELF_DUPLICATE_REVIEW", f"{review_path}.other_source_id", "A source cannot review itself.")
                if other_id in seen_review_ids:
                    audit.error("DUPLICATE_REVIEW", review_path, f"Source {other_id!r} is reviewed more than once.")
                seen_review_ids.add(other_id)
                if review.get("decision") not in {"same_source", "independent"}:
                    audit.error("ENUM", f"{review_path}.decision", "Expected 'same_source' or 'independent'.")
                check_string(review.get("reason"), f"{review_path}.reason", audit)
        check_string(record.get("notes"), f"{path}.notes", audit, allow_empty=True)
        records.append(record)
        if source_id and source_id not in by_id:
            by_id[source_id] = record
    return records, by_id


def validate_signals(raw: Any, audit: Audit) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    catalog = check_object(raw, "signal-catalog.json", {"schema_version", "signals"}, audit)
    if not catalog:
        return [], {}
    validate_schema_version(catalog, "signal-catalog.json", audit)
    raw_signals = catalog.get("signals")
    if not isinstance(raw_signals, list):
        audit.error("TYPE", "signal-catalog.json.signals", "Expected a list.")
        return [], {}
    signals: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw_signal in enumerate(raw_signals):
        path = f"signal-catalog.json.signals[{index}]"
        signal = check_object(raw_signal, path, SIGNAL_REQUIRED, audit)
        if not signal:
            continue
        signal_id = check_string(signal.get("id"), f"{path}.id", audit)
        if signal_id and not ID_RE.fullmatch(signal_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif signal_id and not re.fullmatch(r"sig[-_][a-z0-9][a-z0-9_-]*", signal_id):
            audit.error("ID_PREFIX", f"{path}.id", "Signal IDs must start with sig- or sig_.")
        if signal_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate signal ID {signal_id!r}.")
        for key in ("name", "hypothesis", "decision_relevance", "disconfirming_evidence_needed"):
            check_string(signal.get(key), f"{path}.{key}", audit)
        for key in ("support_citations", "counter_citations", "wtp_citations"):
            check_string_list(signal.get(key), f"{path}.{key}", audit)
        check_string_list(signal.get("alternative_explanations"), f"{path}.alternative_explanations", audit, allow_empty=False)
        if signal.get("claimed_level") not in LEVELS:
            audit.error("ENUM", f"{path}.claimed_level", f"Expected one of {list(LEVELS)}.")
        wtp_statement = signal.get("wtp_statement")
        if wtp_statement is not None:
            check_string(wtp_statement, f"{path}.wtp_statement", audit)
            if not signal.get("wtp_citations"):
                audit.error("WTP_WITHOUT_CITATION", f"{path}.wtp_citations", "A willingness-to-pay statement requires citations.")
        elif signal.get("wtp_citations"):
            audit.error("WTP_CITATION_WITHOUT_STATEMENT", f"{path}.wtp_citations", "Set wtp_statement when WTP citations are present.")
        signals.append(signal)
        if signal_id and signal_id not in by_id:
            by_id[signal_id] = signal
    return signals, by_id


def validate_notes(raw: Any, audit: Audit) -> dict[str, Any]:
    notes = check_object(raw, "research-notes.json", NOTES_REQUIRED, audit)
    if not notes:
        return {}
    validate_schema_version(notes, "research-notes.json", audit)
    observations = notes.get("observations")
    if not isinstance(observations, list):
        audit.error("TYPE", "research-notes.json.observations", "Expected a list.")
    else:
        for index, raw_observation in enumerate(observations):
            path = f"research-notes.json.observations[{index}]"
            observation = check_object(raw_observation, path, {"text", "source_ids"}, audit)
            check_string(observation.get("text"), f"{path}.text", audit)
            check_string_list(observation.get("source_ids"), f"{path}.source_ids", audit, allow_empty=False)
    inferences = notes.get("inferences")
    if not isinstance(inferences, list):
        audit.error("TYPE", "research-notes.json.inferences", "Expected a list.")
    else:
        for index, raw_inference in enumerate(inferences):
            path = f"research-notes.json.inferences[{index}]"
            inference = check_object(raw_inference, path, {"text", "signal_ids"}, audit)
            check_string(inference.get("text"), f"{path}.text", audit)
            check_string_list(inference.get("signal_ids"), f"{path}.signal_ids", audit, allow_empty=False)
    recommendation = check_object(notes.get("recommendation"), "research-notes.json.recommendation", {"text", "signal_ids", "caveats"}, audit)
    check_string(recommendation.get("text"), "research-notes.json.recommendation.text", audit)
    check_string_list(recommendation.get("signal_ids"), "research-notes.json.recommendation.signal_ids", audit)
    check_string_list(recommendation.get("caveats"), "research-notes.json.recommendation.caveats", audit)
    for key in ("next_tests", "coverage_notes"):
        check_string_list(notes.get(key), f"research-notes.json.{key}", audit)
    check_string(notes.get("stop_reason"), "research-notes.json.stop_reason", audit)
    return notes


def detect_repost_cycles(source_by_id: dict[str, dict[str, Any]], audit: Audit) -> None:
    # Iterative traversal avoids recursion limits on adversarially long chains.
    completed: set[str] = set()
    for start_id in source_by_id:
        if start_id in completed:
            continue
        chain: list[str] = []
        positions: dict[str, int] = {}
        current = start_id
        while current in source_by_id and current not in completed:
            if current in positions:
                cycle = chain[positions[current] :] + [current]
                audit.error(
                    "REPOST_CYCLE",
                    f"source-ledger.jsonl:{current}",
                    "Repost cycle: " + " -> ".join(cycle) + ".",
                )
                break
            positions[current] = len(chain)
            chain.append(current)
            target = source_by_id[current].get("repost_of")
            if not isinstance(target, str) or target not in source_by_id:
                break
            current = target
        completed.update(chain)
    for source_id, source in source_by_id.items():
        target = source.get("repost_of")
        if target == source_id:
            audit.error("REPOST_SELF", f"source-ledger.jsonl:{source_id}.repost_of", "A source cannot repost itself.")
        elif isinstance(target, str) and target in source_by_id and source_by_id[target].get("repost_of") is not None:
            audit.error("REPOST_CHAIN", f"source-ledger.jsonl:{source_id}.repost_of", "repost_of must point directly to an origin whose repost_of is null.")


def build_duplicate_groups(
    sources: list[dict[str, Any]], source_by_id: dict[str, dict[str, Any]], audit: Audit
) -> tuple[dict[str, str], dict[str, str]]:
    union = UnionFind(source_by_id)
    url_owner: dict[str, str] = {}
    unit_owner: dict[tuple[str, str], str] = {}
    text_owner: dict[str, str] = {}
    for source in sources:
        source_id = source.get("id")
        if source_id not in source_by_id:
            continue
        target = source.get("repost_of")
        if isinstance(target, str):
            if target not in source_by_id:
                audit.error("BAD_REPOST_REF", f"source-ledger.jsonl:{source_id}.repost_of", f"Unknown source ID {target!r}.")
            else:
                union.union(source_id, target)
        for review in source.get("duplicate_reviews", []):
            if isinstance(review, dict) and review.get("decision") == "same_source" and review.get("other_source_id") in source_by_id:
                union.union(source_id, review["other_source_id"])
        if source.get("visibility") == "public" and isinstance(source.get("url"), str):
            try:
                canonical = canonicalize_url(source["url"])
            except ValueError:
                canonical = ""
        else:
            canonical = ""
        if canonical:
            if canonical in url_owner:
                owner = source_by_id[url_owner[canonical]]
                if owner.get("unit_id") != source.get("unit_id"):
                    audit.error("DUPLICATE_METADATA_CONFLICT", f"source-ledger.jsonl:{source_id}.unit_id", f"Canonical URL is already bound to unit {owner.get('unit_id')!r}.")
                union.union(source_id, url_owner[canonical])
            else:
                url_owner[canonical] = source_id
        unit_key = (str(source.get("platform", "")).casefold(), str(source.get("unit_id", "")).casefold())
        if unit_key[1]:
            if unit_key in unit_owner:
                owner = source_by_id[unit_owner[unit_key]]
                owner_url = owner.get("url") if owner.get("visibility") == "public" else None
                if canonical and isinstance(owner_url, str):
                    try:
                        owner_canonical = canonicalize_url(owner_url)
                    except ValueError:
                        owner_canonical = ""
                    if owner_canonical and owner_canonical != canonical:
                        audit.error("DUPLICATE_METADATA_CONFLICT", f"source-ledger.jsonl:{source_id}.url", f"Platform unit ID is already bound to {owner_canonical!r}.")
                union.union(source_id, unit_owner[unit_key])
            else:
                unit_owner[unit_key] = source_id
        normalized = html.unescape(normalize_text(str(source.get("captured_text", "")))).casefold()
        if len(normalized) >= 80 and word_count(normalized) >= 12:
            content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if content_hash in text_owner:
                union.union(source_id, text_owner[content_hash])
            else:
                text_owner[content_hash] = source_id
    duplicate_root = {source_id: union.find(source_id) for source_id in source_by_id}
    members: dict[str, list[str]] = defaultdict(list)
    explicit_origins: set[str] = set()
    for source_id, root in duplicate_root.items():
        members[root].append(source_id)
        target = source_by_id[source_id].get("repost_of")
        if isinstance(target, str) and target in source_by_id:
            explicit_origins.add(target)

    def published_key(source_id: str) -> tuple[int, datetime, str]:
        source = source_by_id[source_id]
        explicit_priority = 0 if source_id in explicit_origins else 1
        try:
            published = datetime.fromisoformat(str(source.get("published_at", "")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            published = datetime.max.replace(tzinfo=timezone.utc)
        return explicit_priority, published, source_id

    origin_by_root = {root: min(group_members, key=published_key) for root, group_members in members.items()}
    return duplicate_root, origin_by_root


def detect_fuzzy_duplicates(
    sources: list[dict[str, Any]], duplicate_root: dict[str, str], audit: Audit
) -> None:
    eligible: list[tuple[str, list[str], set[tuple[str, ...]]]] = []
    reviewed: dict[tuple[str, str], str] = {}
    for source in sources:
        source_id = source.get("id")
        if not isinstance(source_id, str):
            continue
        for review in source.get("duplicate_reviews", []):
            if isinstance(review, dict) and isinstance(review.get("other_source_id"), str):
                reviewed[tuple(sorted((source_id, review["other_source_id"])))] = str(review.get("decision"))
        tokens = html.unescape(normalize_text(str(source.get("captured_text", "")))).casefold().split()
        if len(tokens) < 30:
            continue
        shingles = {tuple(tokens[index : index + 5]) for index in range(len(tokens) - 4)}
        eligible.append((source_id, tokens, shingles))
    pair_count = len(eligible) * (len(eligible) - 1) // 2
    if pair_count > MAX_FUZZY_PAIRS:
        audit.warn(
            "FUZZY_SCAN_SKIPPED",
            "source-ledger.jsonl",
            f"Fuzzy duplicate scan would require {pair_count} pairs, above the {MAX_FUZZY_PAIRS} pair budget; block or review the long records externally.",
        )
        return
    for left_index, (left_id, left_tokens, left_shingles) in enumerate(eligible):
        for right_id, right_tokens, right_shingles in eligible[left_index + 1 :]:
            if duplicate_root.get(left_id) == duplicate_root.get(right_id):
                continue
            length_ratio = min(len(left_tokens), len(right_tokens)) / max(len(left_tokens), len(right_tokens))
            if length_ratio < 0.90:
                continue
            union_size = len(left_shingles | right_shingles)
            similarity = len(left_shingles & right_shingles) / union_size if union_size else 0.0
            if similarity < 0.90:
                continue
            pair = tuple(sorted((left_id, right_id)))
            if reviewed.get(pair) == "independent":
                continue
            audit.warn(
                "POSSIBLE_DUPLICATE",
                f"source-ledger.jsonl:{pair[0]}/{pair[1]}",
                f"Five-token shingle similarity is {similarity:.0%}; record a duplicate_review decision.",
            )


def reconcile_links(
    queries: list[dict[str, Any]],
    query_by_id: dict[str, dict[str, Any]],
    sources: list[dict[str, Any]],
    source_by_id: dict[str, dict[str, Any]],
    signals: list[dict[str, Any]],
    signal_by_id: dict[str, dict[str, Any]],
    notes: dict[str, Any],
    audit: Audit,
) -> None:
    for query in queries:
        query_id = query.get("id", "")
        for source_id in query.get("included_source_ids", []):
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"query-log.jsonl:{query_id}.included_source_ids", f"Unknown source ID {source_id!r}.")
            elif query_id not in source_by_id[source_id].get("query_ids", []):
                audit.error("QUERY_LINK_MISMATCH", f"query-log.jsonl:{query_id}", f"Source {source_id!r} does not link back to query {query_id!r}.")
        for signal_id in query.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", f"query-log.jsonl:{query_id}.signal_ids", f"Unknown signal ID {signal_id!r}.")
    for source in sources:
        source_id = source.get("id", "")
        for query_id in source.get("query_ids", []):
            if query_id not in query_by_id:
                audit.error("BAD_QUERY_REF", f"source-ledger.jsonl:{source_id}.query_ids", f"Unknown query ID {query_id!r}.")
            elif source_id not in query_by_id[query_id].get("included_source_ids", []):
                audit.error("QUERY_LINK_MISMATCH", f"source-ledger.jsonl:{source_id}", f"Query {query_id!r} does not include source {source_id!r}.")
        for signal_id in source.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", f"source-ledger.jsonl:{source_id}.signal_ids", f"Unknown signal ID {signal_id!r}.")
            else:
                cited = signal_by_id[signal_id].get("support_citations", []) + signal_by_id[signal_id].get("counter_citations", [])
                if source_id not in cited:
                    audit.error("SIGNAL_LINK_MISMATCH", f"source-ledger.jsonl:{source_id}", f"Signal {signal_id!r} does not cite source {source_id!r}.")
    for signal in signals:
        signal_id = signal.get("id", "")
        for field, stance in (("support_citations", "support"), ("counter_citations", "counter")):
            for source_id in signal.get(field, []):
                if source_id not in source_by_id:
                    audit.error("BAD_SOURCE_REF", f"signal-catalog.json:{signal_id}.{field}", f"Unknown source ID {source_id!r}.")
                    continue
                source = source_by_id[source_id]
                if source.get("stance") != stance:
                    audit.error("STANCE_MISMATCH", f"signal-catalog.json:{signal_id}.{field}", f"Source {source_id!r} has stance {source.get('stance')!r}, expected {stance!r}.")
                if signal_id not in source.get("signal_ids", []):
                    audit.error("SIGNAL_LINK_MISMATCH", f"signal-catalog.json:{signal_id}.{field}", f"Source {source_id!r} does not link back to signal {signal_id!r}.")
        for source_id in signal.get("wtp_citations", []):
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"signal-catalog.json:{signal_id}.wtp_citations", f"Unknown source ID {source_id!r}.")
                continue
            source = source_by_id[source_id]
            if source_id not in signal.get("support_citations", []):
                audit.error("WTP_NOT_SUPPORT", f"signal-catalog.json:{signal_id}.wtp_citations", f"WTP source {source_id!r} must also be a support citation.")
            if source.get("promotional") != "no" or source.get("stance") != "support" or not ({"purchase_intent", "observed_payment"} & set(source.get("evidence_types", []))):
                audit.error("UNSUPPORTED_WTP", f"signal-catalog.json:{signal_id}.wtp_citations", f"Source {source_id!r} is not eligible explicit willingness-to-pay evidence.")
    review_decisions: dict[tuple[str, str], str] = {}
    for source in sources:
        source_id = source.get("id", "")
        for review in source.get("duplicate_reviews", []):
            other_id = review.get("other_source_id") if isinstance(review, dict) else None
            if other_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"source-ledger.jsonl:{source_id}.duplicate_reviews", f"Unknown source ID {other_id!r}.")
                continue
            pair = tuple(sorted((source_id, other_id)))
            prior = review_decisions.get(pair)
            decision = review.get("decision")
            if prior is not None and prior != decision:
                audit.error("CONFLICTING_DUPLICATE_REVIEW", f"source-ledger.jsonl:{source_id}.duplicate_reviews", f"Pair {pair[0]!r}/{pair[1]!r} has conflicting decisions.")
            review_decisions[pair] = decision

    for index, observation in enumerate(notes.get("observations", [])):
        if not isinstance(observation, dict):
            continue
        for source_id in observation.get("source_ids", []):
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"research-notes.json.observations[{index}].source_ids", f"Unknown source ID {source_id!r}.")
    for index, inference in enumerate(notes.get("inferences", [])):
        if not isinstance(inference, dict):
            continue
        for signal_id in inference.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", f"research-notes.json.inferences[{index}].signal_ids", f"Unknown signal ID {signal_id!r}.")
    recommendation = notes.get("recommendation", {})
    if isinstance(recommendation, dict):
        if signals and not recommendation.get("signal_ids"):
            audit.warn("RECOMMENDATION_UNLINKED", "research-notes.json.recommendation.signal_ids", "Link the recommendation to at least one signal for strict completion.")
        for signal_id in recommendation.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", "research-notes.json.recommendation.signal_ids", f"Unknown signal ID {signal_id!r}.")


def representative_records(
    source_ids: Iterable[str],
    source_by_id: dict[str, dict[str, Any]],
    duplicate_root: dict[str, str],
    origin_by_root: dict[str, str],
) -> list[dict[str, Any]]:
    roots: set[str] = set()
    for source_id in source_ids:
        if source_id in source_by_id:
            roots.add(duplicate_root.get(source_id, source_id))
    representatives = [source_by_id[origin_by_root[root]] for root in roots if root in origin_by_root]
    return sorted(representatives, key=lambda source: source.get("id", ""))


def calculate_signal_metrics(
    signal: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    duplicate_root: dict[str, str],
    origin_by_root: dict[str, str],
    plan: dict[str, Any],
    queries: list[dict[str, Any]],
    audit: Audit,
) -> dict[str, Any]:
    signal_id = signal.get("id", "")
    support_ids = signal.get("support_citations", [])
    counter_ids = signal.get("counter_citations", [])
    support_roots = {duplicate_root[source_id] for source_id in support_ids if source_id in duplicate_root}
    counter_roots = {duplicate_root[source_id] for source_id in counter_ids if source_id in duplicate_root}
    if support_roots & counter_roots:
        audit.error("CONFLICTING_DUPLICATE_STANCE", f"signal-catalog.json:{signal_id}", "The same duplicate group cannot support and counter the same signal.")
    for field, source_ids in (("support_citations", support_ids), ("counter_citations", counter_ids)):
        for source_id in source_ids:
            if source_id in duplicate_root:
                origin_id = origin_by_root.get(duplicate_root[source_id])
                if origin_id and source_id != origin_id:
                    audit.error("NON_ORIGIN_CITATION", f"signal-catalog.json:{signal_id}.{field}", f"Cite duplicate-group origin {origin_id!r} instead of {source_id!r}.")

    all_support = representative_records(support_ids, source_by_id, duplicate_root, origin_by_root)
    eligible = [source for source in all_support if source.get("stance") == "support" and source.get("promotional") == "no"]
    ineligible = [source for source in all_support if source.get("stance") == "support" and source.get("promotional") in {"yes", "unclear"}]
    unclear = [source for source in all_support if source.get("stance") == "support" and source.get("promotional") == "unclear"]
    counters = representative_records(counter_ids, source_by_id, duplicate_root, origin_by_root)
    authors = {source.get("author_key") for source in eligible if source.get("author_key") != "unknown"}
    threads = {str(source.get("thread_id")).casefold() for source in eligible if source.get("thread_id")}
    communities = {str(source.get("community")).casefold() for source in eligible if source.get("community")}
    platforms = {str(source.get("platform")).casefold() for source in eligible if source.get("platform")}
    ranked_types = sorted({kind for source in eligible for kind in source.get("evidence_types", []) if kind in RANKED_EVIDENCE_TYPES})
    costly_types = sorted({kind for source in eligible for kind in source.get("evidence_types", []) if kind in COSTLY_BEHAVIOR_TYPES})
    risky_promotion = [source for source in all_support if source.get("promotional") in {"yes", "unclear"}]
    promotion_risk_share = len(risky_promotion) / len(all_support) if all_support else 0.0
    unclear_share = len(unclear) / (len(eligible) + len(unclear)) if eligible or unclear else 0.0
    counter_query_count = sum(query.get("intent") == "counter" and signal_id in query.get("signal_ids", []) for query in queries)
    if plan.get("counterevidence_status") == "complete" and counter_query_count:
        countersearch_status = "complete"
    elif counter_query_count or plan.get("counterevidence_status") == "partial":
        countersearch_status = "partial"
    else:
        countersearch_status = "not_searched"
    if counter_query_count == 0:
        audit.warn("SIGNAL_COUNTERQUERY_MISSING", f"signal-catalog.json:{signal_id}", "No counter-oriented query is linked to this signal.")
    if counters:
        counterevidence_level = "present"
    elif countersearch_status == "complete":
        counterevidence_level = "none_found_in_coverage"
    else:
        counterevidence_level = "not_established"

    as_of = date.fromisoformat(plan["as_of"]) if plan.get("as_of") else date.today()
    cutoff = as_of - timedelta(days=int(plan.get("recency_days", 365)))
    recent = 0
    for source in eligible:
        try:
            published = datetime.fromisoformat(
                str(source.get("published_at", "")).replace("Z", "+00:00")
            ).astimezone(timezone.utc).date()
        except ValueError:
            continue
        if cutoff <= published <= as_of:
            recent += 1
    recent_share = recent / len(eligible) if eligible else 0.0

    if not eligible:
        calculated_level = "unsupported"
    elif (
        len(authors) >= 6
        and len(threads) >= 4
        and len(communities) >= 2
        and len(costly_types) >= 2
        and promotion_risk_share <= 0.25
        and countersearch_status == "complete"
    ):
        calculated_level = "well-corroborated"
    elif len(authors) >= 3 and len(threads) >= 2:
        calculated_level = "recurring"
    else:
        calculated_level = "anecdotal"

    claimed_level = signal.get("claimed_level")
    if claimed_level in LEVEL_RANK and LEVEL_RANK[claimed_level] > LEVEL_RANK[calculated_level]:
        audit.error(
            "OVERCLAIMED_LEVEL",
            f"signal-catalog.json:{signal_id}.claimed_level",
            f"Claimed {claimed_level!r}, but the evidence ceiling is {calculated_level!r}.",
        )

    score = (
        min(Decimal("30"), Decimal("6") * len(authors))
        + min(Decimal("20"), Decimal("5") * len(threads))
        + min(Decimal("25"), Decimal("5") * len(ranked_types))
        + min(Decimal("10"), Decimal("5") * len(communities))
        + min(Decimal("5"), Decimal("2.5") * len(platforms))
        + Decimal("10") * Decimal(str(recent_share))
        - Decimal("20") * Decimal(str(unclear_share))
    )
    unrounded_score = max(Decimal("0"), min(Decimal("100"), score))
    evidence_score = float(unrounded_score.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    wtp_records = [source for source in eligible if {"purchase_intent", "observed_payment"} & set(source.get("evidence_types", []))]
    wtp_authors = {source.get("author_key") for source in wtp_records if source.get("author_key") != "unknown"}
    wtp_threads = {str(source.get("thread_id")).casefold() for source in wtp_records if source.get("thread_id")}
    if not wtp_records:
        wtp_status = "none"
    elif len(wtp_authors) >= 3 and len(wtp_threads) >= 2:
        wtp_status = "recurring"
    else:
        wtp_status = "anecdotal"
    has_purchase_intent = any("purchase_intent" in source.get("evidence_types", []) for source in wtp_records)
    has_observed_payment = any("observed_payment" in source.get("evidence_types", []) for source in wtp_records)
    if has_purchase_intent and has_observed_payment:
        wtp_evidence = "mixed"
    elif has_observed_payment:
        wtp_evidence = "observed_payment"
    elif has_purchase_intent:
        wtp_evidence = "purchase_intent"
    else:
        wtp_evidence = "none"

    return {
        "signal_id": signal_id,
        "name": signal.get("name", ""),
        "hypothesis": signal.get("hypothesis", ""),
        "claimed_level": claimed_level,
        "calculated_level": calculated_level,
        "evidence_score": evidence_score,
        "unrounded_score": str(unrounded_score),
        "support_groups": len(eligible),
        "ineligible_support_groups": len(ineligible),
        "distinct_author_keys": len(authors),
        "distinct_threads": len(threads),
        "communities": len(communities),
        "platforms": len(platforms),
        "ranked_evidence_types": ranked_types,
        "costly_behavior_types": costly_types,
        "recent_share": round(recent_share, 4),
        "promotion_risk_share": round(promotion_risk_share, 4),
        "counter_sources": len(counters),
        "countersearch_status": countersearch_status,
        "counterevidence_level": counterevidence_level,
        "counter_query_count": counter_query_count,
        "wtp_status": wtp_status,
        "wtp_evidence": wtp_evidence,
        "wtp_authors": len(wtp_authors),
        "wtp_threads": len(wtp_threads),
        "support_source_ids": [source.get("id") for source in eligible],
        "ineligible_support_source_ids": [source.get("id") for source in ineligible],
        "counter_source_ids": [source.get("id") for source in counters],
    }


def ratio_points(actual: int, target: int) -> float:
    if target <= 0:
        return 0.0
    return 10.0 * min(1.0, actual / target)


def calculate_execution_coverage(
    plan: dict[str, Any],
    queries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    duplicate_root: dict[str, str],
    origin_by_root: dict[str, str],
    audit: Audit,
) -> tuple[float, dict[str, Any]]:
    all_ids = [source.get("id") for source in sources if source.get("id") in duplicate_root]
    unique_sources = representative_records(
        all_ids,
        {source["id"]: source for source in sources if source.get("id")},
        duplicate_root,
        origin_by_root,
    )
    actual = {
        "source_units": len(unique_sources),
        "threads": len({str(source.get("thread_id")).casefold() for source in unique_sources if source.get("thread_id")}),
        "communities": len({str(source.get("community")).casefold() for source in unique_sources if source.get("community")}),
        "platforms": len({str(source.get("platform")).casefold() for source in unique_sources if source.get("platform")}),
        "counter_queries": sum(query.get("intent") == "counter" for query in queries),
    }
    declared_targets = plan.get("coverage_targets", {}) if isinstance(plan.get("coverage_targets"), dict) else {}
    floor = MODE_FLOORS.get(plan.get("mode"), {})
    targets = {
        key: max(
            declared_targets.get(key, 0) if isinstance(declared_targets.get(key, 0), int) else 0,
            floor.get(key, 0),
        )
        for key in ("source_units", "threads", "communities", "platforms", "counter_queries")
    }
    score = 0.0
    for key in ("source_units", "threads", "communities", "platforms", "counter_queries"):
        target = targets.get(key, 0) if isinstance(targets.get(key, 0), int) else 0
        score += ratio_points(actual[key], target)
        if target > 0 and actual[key] < target:
            audit.warn("COVERAGE_SHORTFALL", f"study-plan.json.coverage_targets.{key}", f"Observed {actual[key]} of target {target}.")

    link_issue_codes = {"BAD_SOURCE_REF", "BAD_QUERY_REF", "QUERY_LINK_MISMATCH"}
    if queries and sources and not any(issue.code in link_issue_codes for issue in audit.issues):
        score += 10.0
    else:
        audit.warn("VACUOUS_LINK_COVERAGE", "query-log.jsonl", "Query/source reconciliation earns coverage only when both records exist.")
    if any(query.get("intent") in {"neutral", "counter"} for query in queries):
        score += 10.0
    else:
        audit.warn("NO_BALANCING_QUERY", "query-log.jsonl", "Log at least one neutral or counter-oriented query.")
    if plan.get("counterevidence_status") == "complete" and any(query.get("intent") == "counter" for query in queries):
        score += 10.0
    else:
        audit.warn("COUNTEREVIDENCE_INCOMPLETE", "study-plan.json.counterevidence_status", "Counterevidence must be marked complete and backed by at least one counter query.")

    eligible_support = [source for source in unique_sources if source.get("stance") == "support" and source.get("promotional") == "no"]
    if eligible_support:
        thread_counts = Counter(source.get("thread_id") for source in eligible_support)
        max_share = max(thread_counts.values()) / len(eligible_support)
        if max_share <= 0.5:
            score += 10.0
        else:
            audit.warn("THREAD_CONCENTRATION", "source-ledger.jsonl", f"One thread supplies {max_share:.0%} of eligible supporting source units.")
    else:
        max_share = 0.0
        audit.warn("NO_ELIGIBLE_SUPPORT", "source-ledger.jsonl", "No eligible supporting evidence was captured.")

    risky = sum(source.get("promotional") in {"yes", "unclear"} for source in unique_sources)
    risk_share = risky / len(unique_sources) if unique_sources else 1.0
    if unique_sources and risk_share < 0.25:
        score += 10.0
    else:
        audit.warn("PROMOTION_CONCENTRATION", "source-ledger.jsonl", f"Promotional or unclear sources are {risk_share:.0%} of captured evidence.")

    coverage = {
        "actual": actual,
        "declared_targets": declared_targets,
        "effective_targets": targets,
        "thread_concentration": round(max_share, 4),
        "promotion_risk_share": round(risk_share, 4),
        "query_count": len(queries),
        "truncated_query_count": sum(bool(query.get("truncated")) for query in queries),
    }
    return round(max(0.0, min(100.0, score)), 1), coverage


def semantic_fingerprint(
    plan: dict[str, Any],
    queries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    notes: dict[str, Any],
) -> str:
    fingerprint_sources: list[dict[str, Any]] = []
    for source in sources:
        prepared = dict(source)
        if source.get("visibility") == "supplied_private":
            # Do not publish an offline dictionary oracle for short private
            # responses. The required authorized-file SHA-256 and opaque row
            # locator bind provenance; free text stays outside the public hash.
            for key in ("title", "captured_text", "excerpt", "notes"):
                prepared[key] = "<private-text-withheld-from-public-fingerprint>"
        fingerprint_sources.append(prepared)
    value = {
        "study-plan.json": plan,
        "query-log.jsonl": sorted(queries, key=lambda record: str(record.get("id", ""))),
        "source-ledger.jsonl": sorted(fingerprint_sources, key=lambda record: str(record.get("id", ""))),
        "signal-catalog.json": {"schema_version": SCHEMA_VERSION, "signals": sorted(signals, key=lambda record: str(record.get("id", "")))},
        "research-notes.json": notes,
    }
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def analyze(study_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    audit = Audit()
    plan = validate_plan(load_json(study_dir / "study-plan.json", audit), audit)
    queries, query_by_id = validate_queries(load_jsonl(study_dir / "query-log.jsonl", audit), plan, audit)
    sources, source_by_id = validate_sources(load_jsonl(study_dir / "source-ledger.jsonl", audit), plan, audit)
    signals, signal_by_id = validate_signals(load_json(study_dir / "signal-catalog.json", audit), audit)
    notes = validate_notes(load_json(study_dir / "research-notes.json", audit), audit)

    # Structural validation deliberately precedes cross-record analysis. Once a
    # malformed type or impossible range is present, downstream set/date/graph
    # operations would be both misleading and an avoidable crash surface.
    if audit.errors:
        fingerprint = semantic_fingerprint(plan, queries, sources, signals, notes)
        report = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": VERSION,
            "status": "fail",
            "input_fingerprint": fingerprint,
            "counts": {
                "queries": len(queries),
                "sources": len(sources),
                "duplicate_groups": 0,
                "signals": len(signals),
                "errors": len(audit.errors),
                "warnings": len(audit.warnings),
            },
            "coverage_execution_score": 0.0,
            "coverage": {},
            "signals": [],
            "issues": [asdict(issue) for issue in sorted(audit.issues, key=lambda item: (item.severity, item.code, item.path, item.message))],
        }
        context = {
            "plan": plan,
            "queries": queries,
            "sources": sources,
            "source_by_id": source_by_id,
            "signals": signals,
            "signal_by_id": signal_by_id,
            "notes": notes,
            "duplicate_root": {},
            "origin_by_root": {},
        }
        return report, context

    detect_repost_cycles(source_by_id, audit)
    duplicate_root, origin_by_root = build_duplicate_groups(sources, source_by_id, audit)
    detect_fuzzy_duplicates(sources, duplicate_root, audit)
    reconcile_links(queries, query_by_id, sources, source_by_id, signals, signal_by_id, notes, audit)

    metrics = [
        calculate_signal_metrics(signal, source_by_id, duplicate_root, origin_by_root, plan, queries, audit)
        for signal in signals
    ] if plan else []
    metrics.sort(
        key=lambda item: (
            -Decimal(item["unrounded_score"]),
            -LEVEL_RANK.get(item["calculated_level"], -1),
            -len(item["costly_behavior_types"]),
            -item["distinct_threads"],
            item["signal_id"],
        )
    )
    for rank, metric in enumerate(metrics, start=1):
        metric["rank"] = rank
    execution_score, coverage = calculate_execution_coverage(plan, queries, sources, duplicate_root, origin_by_root, audit) if plan else (0.0, {})

    fingerprint = semantic_fingerprint(plan, queries, sources, signals, notes)
    status = "fail" if audit.errors else ("pass_with_warnings" if audit.warnings else "pass")
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "status": status,
        "input_fingerprint": fingerprint,
        "counts": {
            "queries": len(queries),
            "sources": len(sources),
            "duplicate_groups": len(set(duplicate_root.values())),
            "signals": len(signals),
            "errors": len(audit.errors),
            "warnings": len(audit.warnings),
        },
        "coverage_execution_score": execution_score,
        "coverage": coverage,
        "signals": metrics,
        "issues": [asdict(issue) for issue in sorted(audit.issues, key=lambda item: (item.severity, item.code, item.path, item.message))],
    }
    context = {
        "plan": plan,
        "queries": queries,
        "sources": sources,
        "source_by_id": source_by_id,
        "signals": signals,
        "signal_by_id": signal_by_id,
        "notes": notes,
        "duplicate_root": duplicate_root,
        "origin_by_root": origin_by_root,
    }
    return report, context


def markdown_escape(value: Any) -> str:
    text = html.escape(normalize_text(str(value)), quote=True)
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def markdown_url(value: str) -> str:
    return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def safe_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    first_visible = re.sub(r"^[\s\x00-\x1f]+", "", value)
    if first_visible.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(report: dict[str, Any]) -> str:
    fields = [
        "rank",
        "signal_id",
        "name",
        "hypothesis",
        "calculated_level",
        "claimed_level",
        "evidence_score",
        "coverage_execution_score",
        "support_groups",
        "ineligible_support_groups",
        "distinct_author_keys",
        "distinct_threads",
        "communities",
        "platforms",
        "ranked_evidence_types",
        "costly_behavior_types",
        "recent_share",
        "promotion_risk_share",
        "counter_sources",
        "countersearch_status",
        "counterevidence_level",
        "wtp_status",
        "wtp_evidence",
        "input_fingerprint",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    if report.get("status") == "fail":
        return buffer.getvalue()
    for metric in report.get("signals", []):
        row = dict(metric)
        row["coverage_execution_score"] = report.get("coverage_execution_score", 0)
        row["ranked_evidence_types"] = ";".join(metric.get("ranked_evidence_types", []))
        row["costly_behavior_types"] = ";".join(metric.get("costly_behavior_types", []))
        row["input_fingerprint"] = report.get("input_fingerprint", "")
        writer.writerow({key: safe_csv_cell(row.get(key, "")) for key in fields})
    return buffer.getvalue()


def render_source(source: dict[str, Any]) -> str:
    source_id = markdown_escape(source.get("id", "source"))
    excerpt = markdown_escape(source.get("excerpt", ""))
    published = markdown_escape(str(source.get("published_at", ""))[:10])
    evidence = ", ".join(f"`{markdown_escape(kind)}`" for kind in source.get("evidence_types", [])) or "none"
    flags: list[str] = []
    if source.get("promotional") != "no":
        flags.append(f"promotion: {source.get('promotional')}")
    if source.get("author_key") == "unknown":
        flags.append("author: unknown")
    if source.get("source_status") != "available":
        flags.append(f"source status: {source.get('source_status')}")
    suffix = f"; {markdown_escape(', '.join(flags))}" if flags else ""
    if source.get("visibility") == "public" and isinstance(source.get("url"), str):
        locator = f"[{source_id}]({markdown_url(source['url'])})"
        return f"> \"{excerpt}\" - {locator}, {published}; {evidence}{suffix}"
    locator = f"{source_id} (private supplied record `{markdown_escape(source.get('record_ref', ''))}`)"
    provenance = markdown_escape(source.get("source_file_sha256", ""))
    return f"> Private excerpt withheld - {locator}, {published}; provenance `{provenance}`; {evidence}{suffix}"


def render_findings(report: dict[str, Any], context: dict[str, Any]) -> str:
    fingerprint = report.get("input_fingerprint", "")
    if report.get("status") == "fail":
        lines = [
            "# Community signal build failed",
            "",
            f"Input fingerprint: `{fingerprint}`",
            "",
            "No ranked findings were generated because integrity checks failed.",
            "",
            "## Errors",
            "",
        ]
        for issue in report.get("issues", []):
            if issue.get("severity") == "error":
                lines.append(f"- `{markdown_escape(issue.get('code'))}` at `{markdown_escape(issue.get('path'))}`: {markdown_escape(issue.get('message'))}")
        return "\n".join(lines) + "\n"

    plan = context.get("plan", {})
    queries = sorted(context.get("queries", []), key=lambda query: str(query.get("id", "")))
    notes = context.get("notes", {})
    source_by_id = context.get("source_by_id", {})
    signal_by_id = context.get("signal_by_id", {})
    lines = [
        "# Community signal findings",
        "",
        f"Input fingerprint: `{fingerprint}`",
        "",
        f"**Decision:** {markdown_escape(plan.get('decision', ''))}",
        "",
        f"**Research question:** {markdown_escape(plan.get('question', ''))}",
        "",
        f"**Evidence cutoff:** {markdown_escape(plan.get('as_of', ''))} | **Mode:** `{markdown_escape(plan.get('mode', ''))}` | **Coverage-execution score:** {report.get('coverage_execution_score', 0)}/100",
        "",
        "> This report ranks evidence observed in the declared sample. It does not estimate market size, prevalence, revenue, or population-level demand. Engagement is not scored.",
        "",
        "> The offline audit proves internal ledger consistency and reproducible output, not remote-page authenticity, account identity, semantic classification, search completeness, or representativeness.",
        "",
        "## Ranked hypotheses",
        "",
        "| Rank | Signal | Evidence label | Evidence score | Author keys | Threads | Excluded cited support | Counter sources | WTP |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for metric in report.get("signals", []):
        lines.append(
            f"| {metric['rank']} | {markdown_escape(metric['name'])} | `{metric['calculated_level']}` | {metric['evidence_score']} | {metric['distinct_author_keys']} | {metric['distinct_threads']} | {metric['ineligible_support_groups']} | {metric['counter_sources']} | `{metric['wtp_status']}` |"
        )
    if not report.get("signals"):
        lines.append("| — | No hypotheses supplied | `unsupported` | 0 | 0 | 0 | 0 | 0 | `none` |")

    for metric in report.get("signals", []):
        signal = signal_by_id.get(metric["signal_id"], {})
        lines.extend(
            [
                "",
                f"## {metric['rank']}. {markdown_escape(metric['name'])}",
                "",
                f"**Hypothesis:** {markdown_escape(metric['hypothesis'])}",
                "",
                f"**Decision relevance:** {markdown_escape(signal.get('decision_relevance', ''))}",
                "",
                f"**Evidence ceiling:** `{metric['calculated_level']}` from {metric['distinct_author_keys']} distinct observed author keys across {metric['distinct_threads']} threads and {metric['communities']} communities. Score {metric['evidence_score']}/100 within this sample.",
                "",
                f"**Countersearch:** `{metric['countersearch_status']}`; counterevidence `{metric['counterevidence_level']}`. **Costly behavior observed:** {', '.join(metric['costly_behavior_types']) or 'none'}.",
                "",
                "### Supporting evidence",
                "",
            ]
        )
        if metric.get("support_source_ids"):
            for source_id in metric["support_source_ids"]:
                lines.append(render_source(source_by_id[source_id]))
                lines.append("")
        else:
            lines.extend(["No eligible supporting evidence.", ""])
        if metric.get("ineligible_support_source_ids"):
            lines.extend(
                [
                    "### Cited context excluded from positive counts",
                    "",
                    "These promotional or promotion-unclear records remain visible but do not affect labels, ranks, or willingness-to-pay counts.",
                    "",
                ]
            )
            for source_id in metric["ineligible_support_source_ids"]:
                lines.append(render_source(source_by_id[source_id]))
                lines.append("")
        lines.extend(["### Counterevidence", ""])
        if metric.get("counter_source_ids"):
            for source_id in metric["counter_source_ids"]:
                lines.append(render_source(source_by_id[source_id]))
                lines.append("")
        elif metric.get("countersearch_status") == "complete":
            lines.extend(["No counterexample was found in the searched coverage; this does not mean none exists.", ""])
        elif metric.get("countersearch_status") == "partial":
            lines.extend(["No cited counterexample is present, and the countersearch is incomplete.", ""])
        else:
            lines.extend(["No counter-oriented search is established for this signal; no absence claim can be made.", ""])
        alternatives = signal.get("alternative_explanations", [])
        lines.extend(["### What could falsify or reframe this", ""])
        for alternative in alternatives:
            lines.append(f"- {markdown_escape(alternative)}")
        lines.append(f"- Evidence needed: {markdown_escape(signal.get('disconfirming_evidence_needed', ''))}")
        if signal.get("wtp_statement"):
            lines.extend(["", f"**Willingness-to-pay observation ({metric['wtp_status']}, {metric['wtp_evidence']}):** {markdown_escape(signal['wtp_statement'])}"])

    coverage = report.get("coverage", {})
    lines.extend(["", "## Scope, search coverage, and limitations", ""])
    date_window = plan.get("date_window", {}) if isinstance(plan.get("date_window"), dict) else {}
    scope = plan.get("scope", {}) if isinstance(plan.get("scope"), dict) else {}
    lines.append(
        f"- Date window: {markdown_escape(date_window.get('start', ''))} through {markdown_escape(date_window.get('end', ''))}; evidence cutoff {markdown_escape(plan.get('as_of', ''))}."
    )
    lines.append(f"- Platforms in scope: {markdown_escape(', '.join(scope.get('platforms', [])))}.")
    lines.append(f"- Communities in scope: {markdown_escape(', '.join(scope.get('communities', [])))}.")
    lines.append(f"- Languages in scope: {markdown_escape(', '.join(scope.get('languages', [])))}.")
    lines.append("- Inclusion criteria: " + markdown_escape("; ".join(plan.get("inclusion_criteria", []))) + ".")
    lines.append("- Exclusion criteria: " + markdown_escape("; ".join(plan.get("exclusion_criteria", []))) + ".")
    actual = coverage.get("actual", {})
    targets = coverage.get("effective_targets", {})
    for key in ("source_units", "threads", "communities", "platforms", "counter_queries"):
        lines.append(f"- {markdown_escape(key.replace('_', ' ').title())}: {actual.get(key, 0)} observed / {targets.get(key, 0)} target.")
    lines.append(f"- Truncated queries: {coverage.get('truncated_query_count', 0)} of {coverage.get('query_count', 0)}.")
    lines.append(f"- Largest eligible-support thread share: {coverage.get('thread_concentration', 0):.0%}.")
    lines.append(f"- Promotional or unclear source share: {coverage.get('promotion_risk_share', 0):.0%}.")
    for limitation in plan.get("limitations", []):
        lines.append(f"- Declared limitation: {markdown_escape(limitation)}")
    for coverage_note in notes.get("coverage_notes", []):
        lines.append(f"- Coverage note: {markdown_escape(coverage_note)}")

    lines.extend(
        [
            "",
            "### Query ledger",
            "",
            "| Query ID | Run at | Platform | Intent | Query | Sort | Seen / screened | Pages | Truncated | Included units |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for query in queries:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{markdown_escape(query.get('id', ''))}`",
                    markdown_escape(query.get("run_at", "")),
                    markdown_escape(query.get("platform", "")),
                    f"`{markdown_escape(query.get('intent', ''))}`",
                    markdown_escape(query.get("query", "")),
                    markdown_escape(query.get("sort", "")),
                    f"{query.get('results_seen', 0)} / {query.get('results_screened', 0)}",
                    str(query.get("pages_seen", 0)),
                    "yes" if query.get("truncated") else "no",
                    str(len(query.get("included_source_ids", []))),
                ]
            )
            + " |"
        )
    if not queries:
        lines.append("| - | - | - | - | No queries logged | - | 0 / 0 | 0 | no | 0 |")

    lines.extend(["", "## Interpretation and next action", ""])
    for observation in notes.get("observations", []):
        if isinstance(observation, dict):
            citations = ", ".join(f"`{markdown_escape(source_id)}`" for source_id in observation.get("source_ids", []))
            lines.append(f"- Cited observation ({citations}): {markdown_escape(observation.get('text', ''))}")
    for inference in notes.get("inferences", []):
        if isinstance(inference, dict):
            signals = ", ".join(f"`{markdown_escape(signal_id)}`" for signal_id in inference.get("signal_ids", []))
            lines.append(f"- Researcher inference ({signals}): {markdown_escape(inference.get('text', ''))}")
    recommendation = notes.get("recommendation", {}) if isinstance(notes.get("recommendation"), dict) else {}
    recommendation_signals = ", ".join(f"`{markdown_escape(signal_id)}`" for signal_id in recommendation.get("signal_ids", []))
    lines.extend(["", f"**Recommendation ({recommendation_signals}):** {markdown_escape(recommendation.get('text', ''))}"])
    for caveat in recommendation.get("caveats", []):
        lines.append(f"- Recommendation caveat: {markdown_escape(caveat)}")
    lines.extend(["", "**Next tests:**"])
    for next_test in notes.get("next_tests", []):
        lines.append(f"- {markdown_escape(next_test)}")
    lines.extend(["", f"**Stop reason:** {markdown_escape(notes.get('stop_reason', ''))}", ""])

    warnings = [issue for issue in report.get("issues", []) if issue.get("severity") == "warning"]
    if warnings:
        lines.extend(["## Audit warnings", ""])
        for issue in warnings:
            lines.append(f"- `{markdown_escape(issue.get('code'))}` at `{markdown_escape(issue.get('path'))}`: {markdown_escape(issue.get('message'))}")
        lines.append("")
    return "\n".join(lines)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OSError(f"Refusing to write through symlinked directory: {path.parent}")
    descriptor, temp_name = tempfile.mkstemp(prefix=".csr-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def ensure_study_dir(path: Path, *, create: bool = False) -> Path:
    if path.exists() and path.is_symlink():
        raise ValueError(f"Refusing symlinked study directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"Study directory does not exist: {path}")
    return path.resolve()


def artifact_contents(report: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    csv_content = render_csv(report)
    findings_content = render_findings(report, context)
    committed_report = dict(report)
    committed_report["artifact_hashes"] = {
        "signals.csv": sha256_text(csv_content),
        "findings.md": sha256_text(findings_content),
    }
    audit_content = json.dumps(committed_report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    return {"signals.csv": csv_content, "findings.md": findings_content, "audit.json": audit_content}


def safe_artifacts_dir(study_dir: Path, *, create: bool) -> Path:
    artifacts_dir = study_dir / "artifacts"
    if artifacts_dir.exists():
        if artifacts_dir.is_symlink() or not artifacts_dir.resolve().is_relative_to(study_dir.resolve()):
            raise ValueError(f"Refusing artifact directory outside the study root: {artifacts_dir}")
        if not artifacts_dir.is_dir():
            raise ValueError(f"Artifact path is not a directory: {artifacts_dir}")
    elif create:
        artifacts_dir.mkdir(parents=False)
    for name in ARTIFACT_NAMES:
        if (artifacts_dir / name).is_symlink():
            raise ValueError(f"Refusing symlinked artifact target: {artifacts_dir / name}")
    return artifacts_dir


def recover_interrupted_artifact_swap(study_dir: Path) -> bool:
    """Restore the prior generation when a hard stop landed between directory renames."""
    artifacts_dir = study_dir / "artifacts"
    if artifacts_dir.exists():
        return False
    backups = sorted(
        path
        for path in study_dir.iterdir()
        if path.name.startswith(".csr-artifacts-backup-") and path.is_dir()
    )
    if not backups:
        return False
    if len(backups) != 1:
        raise ValueError("Multiple interrupted artifact backups exist; inspect them before retrying the build")
    backup = backups[0]
    if backup.is_symlink() or not backup.resolve().is_relative_to(study_dir.resolve()):
        raise ValueError("Refusing an unsafe interrupted artifact backup")
    entries = list(backup.iterdir())
    if any(
        entry.name not in ARTIFACT_NAMES
        or entry.is_symlink()
        or not entry.is_file()
        or not entry.resolve().is_relative_to(backup.resolve())
        for entry in entries
    ):
        raise ValueError("Interrupted artifact backup contains unexpected or unsafe entries")
    os.replace(backup, artifacts_dir)
    for stage in sorted(study_dir.iterdir()):
        if not stage.name.startswith(".csr-artifacts-stage-"):
            continue
        if stage.is_symlink() or not stage.is_dir() or not stage.resolve().is_relative_to(study_dir.resolve()):
            raise ValueError("Refusing an unsafe interrupted artifact stage")
        shutil.rmtree(stage)
    return True


def build_artifacts(study_dir: Path) -> tuple[dict[str, Any], int]:
    recover_interrupted_artifact_swap(study_dir)
    report, context = analyze(study_dir)
    if report.get("status") == "fail":
        return report, 1
    contents = artifact_contents(report, context)
    artifacts_dir = safe_artifacts_dir(study_dir, create=True)

    # Build the complete set beside the live directory, then switch directories.
    # Per-file atomic replacement can still leave a mixed generation when the
    # second or third write fails. A staged set keeps the previous generation
    # untouched until every new artifact has been written and fsynced.
    expected_names = set(contents)
    unexpected = sorted(path.name for path in artifacts_dir.iterdir() if path.name not in expected_names)
    if unexpected:
        joined = ", ".join(unexpected)
        raise ValueError(f"Refusing to replace an artifact directory with unexpected entries: {joined}")

    stage_dir = Path(tempfile.mkdtemp(prefix=".csr-artifacts-stage-", dir=study_dir))
    backup_dir = Path(tempfile.mkdtemp(prefix=".csr-artifacts-backup-", dir=study_dir))
    backup_dir.rmdir()  # Reserve a same-filesystem, collision-resistant path for the directory swap.
    previous_moved = False
    new_installed = False
    try:
        # audit.json remains last within the staged generation and acts as its
        # commit marker for readers that inspect the directory after the swap.
        for name in ARTIFACT_NAMES:
            atomic_write_text(stage_dir / name, contents[name])

        os.replace(artifacts_dir, backup_dir)
        previous_moved = True
        try:
            os.replace(stage_dir, artifacts_dir)
            new_installed = True
        except BaseException:
            # The live path is absent at this point; restore the complete old
            # generation before propagating the installation failure.
            os.replace(backup_dir, artifacts_dir)
            previous_moved = False
            raise
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        if new_installed and previous_moved and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
    committed = strict_json_loads(contents["audit.json"])
    return committed, 0


def audit_artifacts(study_dir: Path, report: dict[str, Any], context: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    artifacts_dir = safe_artifacts_dir(study_dir, create=False)
    expected = artifact_contents(report, context)
    if not artifacts_dir.exists():
        return [Issue("error", "MISSING_ARTIFACT", "artifacts", "Run build before the final audit.")]
    actual_names = {path.name for path in artifacts_dir.iterdir() if path.is_file()}
    extra = sorted(actual_names - expected.keys())
    for name in extra:
        issues.append(Issue("error", "EXTRA_ARTIFACT", f"artifacts/{name}", "Generated artifact directory contains an unexpected file."))
    for name, expected_content in expected.items():
        path = artifacts_dir / name
        if not path.is_file():
            issues.append(Issue("error", "MISSING_ARTIFACT", f"artifacts/{name}", "Run build to regenerate artifacts."))
            continue
        try:
            actual_content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(Issue("error", "BAD_ARTIFACT", f"artifacts/{name}", "Generated artifact is not UTF-8."))
            continue
        if actual_content != expected_content:
            issues.append(Issue("error", "MODIFIED_ARTIFACT", f"artifacts/{name}", "Artifact bytes do not match a fresh deterministic build."))
    return issues


def terminal_safe(value: Any) -> str:
    text = str(value)
    return "".join(
        character
        if ord(character) >= 32 and not 127 <= ord(character) <= 159
        else f"\\u{ord(character):04x}"
        for character in text
    )


def print_report(report: dict[str, Any], *, json_output: bool = False, strict: bool = False) -> int:
    issues = report.get("issues", [])
    errors = sum(issue.get("severity") == "error" for issue in issues)
    warnings = sum(issue.get("severity") == "warning" for issue in issues)
    if json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(terminal_safe(f"community-signal {VERSION}: {report.get('status', 'unknown')}"))
        print(terminal_safe(f"inputs: {report.get('input_fingerprint', 'unknown')}"))
        print(f"coverage execution: {report.get('coverage_execution_score', 0)}/100 | errors: {errors} | warnings: {warnings}")
        for issue in issues:
            print(terminal_safe(f"{issue.get('severity', '').upper():7} {issue.get('code')} {issue.get('path')}: {issue.get('message')}"))
    return 1 if errors or (strict and warnings) else 0


def init_study(args: argparse.Namespace) -> int:
    study_dir = ensure_study_dir(Path(args.study_dir), create=True)
    existing = [name for name in (*INPUT_FILES, ".author-key") if (study_dir / name).exists()]
    if existing:
        raise ValueError("Refusing to overwrite existing input files: " + ", ".join(existing))
    raw_study_id = args.study_id or study_dir.name.lower().replace(" ", "-")
    study_id = re.sub(r"[^a-z0-9._-]+", "-", raw_study_id).strip("-._")[:80]
    if not STUDY_ID_RE.fullmatch(study_id):
        raise ValueError("study ID must contain 3-80 lowercase letters, digits, dots, underscores, or hyphens")
    try:
        as_of_date = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError as exc:
        raise ValueError("--as-of must be an ISO date in YYYY-MM-DD form") from exc
    if args.recency_days > (as_of_date - date.min).days:
        raise ValueError(
            f"--recency-days must not reach before {date.min.isoformat()} for as_of {as_of_date.isoformat()}"
        )
    plan = {
        "schema_version": SCHEMA_VERSION,
        "study_id": study_id,
        "question": args.question,
        "decision": args.decision,
        "mode": args.mode,
        "as_of": as_of_date.isoformat(),
        "recency_days": args.recency_days,
        "date_window": {"start": (as_of_date - timedelta(days=args.recency_days)).isoformat(), "end": as_of_date.isoformat()},
        "population": "Define the actor and situation in scope.",
        "scope": {"platforms": ["reddit"], "communities": ["Define communities"], "languages": ["en"]},
        "inclusion_criteria": ["First-person pain, request, workaround, adoption, constraint, satisfaction, or explicit purchase evidence"],
        "exclusion_criteria": ["Untraceable summaries and promotion without independent evidence"],
        "coverage_targets": dict(MODE_FLOORS[args.mode]),
        "counterevidence_status": "planned",
        "stop_condition": "Coverage met or two successive query families add no new mechanism.",
        "limitations": [],
    }
    catalog = {"schema_version": SCHEMA_VERSION, "signals": []}
    notes = {
        "schema_version": SCHEMA_VERSION,
        "observations": [],
        "inferences": [],
        "recommendation": {"text": "Complete after analyzing the audited evidence.", "signal_ids": [], "caveats": []},
        "next_tests": [],
        "coverage_notes": [],
        "stop_reason": "Complete after the declared stop condition is reached.",
    }
    atomic_write_text(study_dir / "study-plan.json", json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(study_dir / "query-log.jsonl", "")
    atomic_write_text(study_dir / "source-ledger.jsonl", "")
    atomic_write_text(study_dir / "signal-catalog.json", json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(study_dir / "research-notes.json", json.dumps(notes, indent=2, ensure_ascii=False) + "\n")
    secret_path = study_dir / ".author-key"
    atomic_write_text(secret_path, secrets.token_hex(32) + "\n")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    print(terminal_safe(f"Initialized {args.mode} study at {study_dir}"))
    return 0


def command_build(args: argparse.Namespace) -> int:
    study_dir = ensure_study_dir(Path(args.study_dir))
    report, exit_code = build_artifacts(study_dir)
    print_report(report, json_output=args.json)
    if exit_code == 0 and not args.json:
        print(terminal_safe(f"artifacts: {study_dir / 'artifacts'}"))
    return exit_code


def command_audit(args: argparse.Namespace) -> int:
    study_dir = ensure_study_dir(Path(args.study_dir))
    report, context = analyze(study_dir)
    artifact_issues = [] if report.get("status") == "fail" else audit_artifacts(study_dir, report, context)
    if artifact_issues:
        report["issues"].extend(asdict(issue) for issue in artifact_issues)
        report["issues"].sort(key=lambda item: (item["severity"], item["code"], item["path"], item["message"]))
        report["counts"]["errors"] += sum(issue.severity == "error" for issue in artifact_issues)
        report["counts"]["warnings"] += sum(issue.severity == "warning" for issue in artifact_issues)
        report["status"] = "fail" if report["counts"]["errors"] else "pass_with_warnings"
    return print_report(report, json_output=args.json, strict=args.strict)


def command_validate(args: argparse.Namespace) -> int:
    study_dir = ensure_study_dir(Path(args.study_dir))
    report, _ = analyze(study_dir)
    return print_report(report, json_output=args.json, strict=args.strict)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a study without overwriting existing inputs.")
    init_parser.add_argument("study_dir")
    init_parser.add_argument("--question", required=True)
    init_parser.add_argument("--decision", required=True)
    init_parser.add_argument("--mode", choices=sorted(MODES), default="standard")
    init_parser.add_argument("--study-id")
    init_parser.add_argument("--as-of")
    init_parser.add_argument("--recency-days", type=int, default=365)
    init_parser.set_defaults(func=init_study)

    validate_parser = subparsers.add_parser("validate", help="Validate inputs without writing artifacts.")
    validate_parser.add_argument("study_dir")
    validate_parser.add_argument("--strict", action="store_true", help="Return nonzero for warnings as well as errors.")
    validate_parser.add_argument("--json", action="store_true", help="Print the audit as JSON.")
    validate_parser.set_defaults(func=command_validate)

    build_parser_command = subparsers.add_parser("build", help="Validate inputs and atomically generate artifacts.")
    build_parser_command.add_argument("study_dir")
    build_parser_command.add_argument("--json", action="store_true", help="Print the audit as JSON.")
    build_parser_command.set_defaults(func=command_build)

    audit_parser = subparsers.add_parser("audit", help="Recompute checks and byte-compare generated artifacts.")
    audit_parser.add_argument("study_dir")
    audit_parser.add_argument("--strict", action="store_true", help="Return nonzero for warnings as well as errors.")
    audit_parser.add_argument("--json", action="store_true", help="Print the audit as JSON.")
    audit_parser.set_defaults(func=command_audit)

    canonical_parser = subparsers.add_parser("canonicalize", help="Print a deterministic canonical form for a URL.")
    canonical_parser.add_argument("url")
    canonical_parser.set_defaults(func=lambda args: (print(canonicalize_url(args.url)) or 0))

    author_parser = subparsers.add_parser("author-key", help="Pseudonymize one author handle read from standard input.")
    author_parser.add_argument("--study-dir", required=True)

    def author_key(args: argparse.Namespace) -> int:
        # Native Windows pipelines and Python can otherwise disagree about the
        # active code page. Treat stdin as an explicit UTF-8 byte contract so
        # the same Unicode handle produces the same study-local key on every OS.
        if hasattr(sys.stdin, "buffer"):
            try:
                raw = sys.stdin.buffer.read().decode("utf-8-sig").strip()
            except UnicodeDecodeError as exc:
                raise ValueError("author-key stdin must be UTF-8 encoded") from exc
        else:  # Supports embedded/test text streams without a buffer.
            raw = sys.stdin.read().strip()
        if not raw:
            raise ValueError("author-key expects a non-empty handle on standard input")
        if DISALLOWED_CONTROL_RE.search(raw) or any(character in raw for character in "\r\n\t"):
            raise ValueError("author-key handle contains a disallowed control character")
        study_dir = ensure_study_dir(Path(args.study_dir))
        secret_path = study_dir / ".author-key"
        if not secret_path.is_file() or secret_path.is_symlink():
            raise ValueError("study does not contain the private .author-key created by init")
        secret = bytes.fromhex(secret_path.read_text(encoding="utf-8").strip())
        if len(secret) != 32:
            raise ValueError(".author-key is malformed")
        normalized = unicodedata.normalize("NFKC", raw).casefold().encode("utf-8")
        digest = hmac.new(secret, normalized, hashlib.sha256).hexdigest()[:16]
        print("author:" + digest)
        return 0

    author_parser.set_defaults(func=author_key)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "recency_days", 1) <= 0:
        parser.error("--recency-days must be greater than zero")
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(terminal_safe(f"error: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
