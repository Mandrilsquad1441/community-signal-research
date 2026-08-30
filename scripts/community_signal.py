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
import math
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


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
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z0-9_][A-Za-z0-9._%+-]*@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9_])"
)
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\+?[0-9][0-9 .()\-]{7,}[0-9])(?![A-Za-z0-9_])"
)
OPAQUE_RECORD_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
DISALLOWED_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
BIDI_FORMAT_RE = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref_source",
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_JSONL_EXTRA_LINES = 1_000
MAX_CAPTURED_TEXT = 100_000
MAX_GENERAL_STRING = 20_000
MAX_EXCERPT_CHARS = 500
MAX_FUZZY_PAIRS = 100_000
MAX_FUZZY_SHINGLE_WORK = 5_000_000
MAX_FUZZY_STORED_SHINGLES = 200_000
MAX_JSON_DEPTH = 100
MAX_JSON_NUMBER_CHARS = 256
MAX_DATE_CHARS = 10
MAX_TIMESTAMP_CHARS = 64
MAX_PRIVATE_NGRAMS = 200_000
MIN_PRIVATE_IDENTIFIER_CHARS = 12
MAX_AUTHOR_HANDLE_BYTES = 4_096
MAX_LIST_ITEMS = 10_000
MAX_OBJECT_FIELDS = 10_000
MAX_ISSUES = 10_000
MAX_STUDY_DIRECTORY_ENTRIES = 10_000
MAX_TOTAL_CITATION_REFERENCES = 50_000
MAX_GENERATED_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_GITIGNORE_BYTES = 1 * 1024 * 1024
MAX_REDDIT_ID_CHARS = 32
MAX_NATIVE_DECIMAL_ID_CHARS = 20
MAX_PORT_CHARS = 5
TRANSACTION_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
MIN_EXACT_DUPLICATE_CHARS = 80
MIN_EXACT_DUPLICATE_WORDS = 12
MIN_SHORT_EXACT_CHARS = 20
MIN_SHORT_EXACT_WORDS = 4
INIT_RECOMMENDATION = "Complete after analyzing the audited evidence."
INIT_STOP_REASON = "Complete after the declared stop condition is reached."
PINNED_UNICODE = unicodedata.ucd_3_2_0
PINNED_WHITESPACE_CODEPOINTS = frozenset(
    {
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        0x180E,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    }
)
ASCII_CASE_TRANSLATION = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)
UNRESERVED_URL_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
PATH_SAFE_URL_BYTES = UNRESERVED_URL_BYTES | frozenset(b"!$&'()*+,;=:@/")
FRAGMENT_SAFE_URL_BYTES = PATH_SAFE_URL_BYTES | frozenset(b"?")
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "credential",
    "credentials",
    "id_token",
    "jsessionid",
    "jwt",
    "key",
    "key_pair_id",
    "oauth_token",
    "passwd",
    "password",
    "private_token",
    "refresh_token",
    "samlresponse",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sharedaccesssignature",
    "sig",
    "signature",
    "token",
}
NONPUBLIC_HOST_SUFFIXES = (
    ".home.arpa",
    ".internal",
    ".lan",
    ".local",
    ".localdomain",
    ".localhost",
)
SPECIAL_USE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
SPECIAL_USE_IPV6_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "::/96",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/23",
        "2001:db8::/32",
        "2002::/16",
        "3ffe::/16",
        "3fff::/20",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)
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
        self._issue_limit_reported = False

    def _add(self, issue: Issue) -> None:
        if len(self.issues) < MAX_ISSUES:
            self.issues.append(issue)
        elif not self._issue_limit_reported:
            self.issues.append(
                Issue(
                    "error",
                    "ISSUE_LIMIT",
                    "inputs",
                    f"Validation produced more than {MAX_ISSUES} issues; fix the reported structural errors before retrying.",
                )
            )
            self._issue_limit_reported = True

    def error(self, code: str, path: str, message: str) -> None:
        self._add(Issue("error", code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self._add(Issue("warning", code, path, message))

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def issue_limit_reached(self) -> bool:
        return self._issue_limit_reported

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


class DuplicateKeyError(ValueError):
    pass


class JsonDepthError(ValueError):
    pass


class PublicIdentityError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


class BoundedFileTooLargeError(ValueError):
    pass


class UnsafeFileReadError(ValueError):
    pass


class UnionFind:
    def __init__(self, keys: Iterable[str]) -> None:
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        path: list[str] = []
        current = key
        while self.parent[current] != current:
            path.append(current)
            current = self.parent[current]
        for member in path:
            self.parent[member] = current
        return current

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
            if len(key) > MAX_GENERAL_STRING:
                raise ValueError(f"JSON object key exceeds {MAX_GENERAL_STRING} characters")
            try:
                key.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("JSON object key contains an invalid Unicode surrogate") from exc
            if BIDI_FORMAT_RE.search(key):
                raise ValueError("JSON object key contains a bidirectional formatting control")
            if DISALLOWED_CONTROL_RE.search(key):
                raise ValueError("JSON object key contains a disallowed control character")
            if key in result:
                raise DuplicateKeyError(f"duplicate object key {key!r}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> Any:
        raise ValueError(f"non-finite JSON number {constant!r} is not allowed")

    def parse_integer(token: str) -> int:
        if len(token.lstrip("-")) > MAX_JSON_NUMBER_CHARS:
            raise ValueError(f"JSON integer exceeds {MAX_JSON_NUMBER_CHARS} digits")
        return int(token)

    def parse_decimal(token: str) -> float:
        if len(token) > MAX_JSON_NUMBER_CHARS:
            raise ValueError(f"JSON number exceeds {MAX_JSON_NUMBER_CHARS} characters")
        number = float(token)
        if not math.isfinite(number):
            raise ValueError(f"non-finite JSON number {token!r} is not allowed")
        return number

    depth = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise JsonDepthError(f"JSON nesting exceeds the supported depth of {MAX_JSON_DEPTH}")
        elif character in "]}":
            depth = max(0, depth - 1)
    try:
        return json.loads(
            value,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
            parse_int=parse_integer,
            parse_float=parse_decimal,
        )
    except RecursionError as exc:
        raise JsonDepthError(f"JSON nesting exceeds the supported depth of {MAX_JSON_DEPTH}") from exc


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    """Apply a runtime-independent Unicode 3.2 NFKC and whitespace policy."""
    normalized = PINNED_UNICODE.normalize("NFKC", value)
    result: list[str] = []
    pending_space = False
    for character in normalized:
        if ord(character) in PINNED_WHITESPACE_CODEPOINTS:
            pending_space = bool(result)
            continue
        if pending_space:
            result.append(" ")
            pending_space = False
        result.append(character)
    return "".join(result)


def strip_pinned_whitespace(value: str) -> str:
    """Strip only the project-pinned whitespace set from both ends."""
    start = 0
    end = len(value)
    while start < end and ord(value[start]) in PINNED_WHITESPACE_CODEPOINTS:
        start += 1
    while end > start and ord(value[end - 1]) in PINNED_WHITESPACE_CODEPOINTS:
        end -= 1
    return value[start:end]


def ascii_casefold(value: str) -> str:
    """Apply the project-pinned case-insensitive mapping used for identifiers."""
    return value.translate(ASCII_CASE_TRANSLATION)


def normalized_label(value: Any) -> str:
    """Normalize user-supplied labels before equality or diversity checks."""
    return ascii_casefold(normalize_text(str(value)))


def canonical_platform(value: Any) -> str:
    platform = normalized_label(value)
    return "hackernews" if platform == "hn" else platform


def word_count(value: str) -> int:
    normalized = normalize_text(value)
    return 0 if not normalized else normalized.count(" ") + 1


def is_eligible_positive_source(source: dict[str, Any]) -> bool:
    return (
        source.get("stance") == "support"
        and source.get("promotional") == "no"
        and source.get("author_key") != "unknown"
    )


def parse_datetime(value: Any, path: str, audit: Audit) -> datetime | None:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected an ISO 8601 timestamp string.")
        return None
    if (
        len(value) > MAX_TIMESTAMP_CHARS
        or DISALLOWED_CONTROL_RE.search(value)
        or BIDI_FORMAT_RE.search(value)
    ):
        audit.error("TIMESTAMP", path, "Invalid ISO 8601 timestamp.")
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        audit.error("TIMESTAMP", path, "Invalid ISO 8601 timestamp.")
        return None
    if not TIMESTAMP_RE.fullmatch(value):
        audit.error("TIMESTAMP", path, "Invalid ISO 8601 timestamp.")
        return None
    try:
        parseable = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(parseable)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            audit.error("TIMEZONE", path, "Timestamp must include a timezone or Z suffix.")
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        audit.error("TIMESTAMP", path, "Invalid ISO 8601 timestamp.")
        return None


def parse_date(value: Any, path: str, audit: Audit) -> date | None:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected an ISO 8601 date string.")
        return None
    if len(value) > MAX_DATE_CHARS or DISALLOWED_CONTROL_RE.search(value) or BIDI_FORMAT_RE.search(value):
        audit.error("DATE", path, "Invalid ISO 8601 date.")
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        audit.error("DATE", path, "Invalid ISO 8601 date.")
        return None
    if not DATE_RE.fullmatch(value):
        audit.error("DATE", path, "Invalid ISO 8601 date.")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        audit.error("DATE", path, "Invalid ISO 8601 date.")
        return None


def is_special_use_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Apply a project-pinned literal-IP policy independent of Python releases."""
    networks = SPECIAL_USE_IPV4_NETWORKS if address.version == 4 else SPECIAL_USE_IPV6_NETWORKS
    return any(address in network for network in networks)


def _normalize_percent_component(value: str, safe_bytes: frozenset[int]) -> str:
    """Encode raw Unicode/unsafe bytes, decode unreserved escapes, and preserve reserved escapes."""
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "%":
            if index + 2 >= len(value) or not re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3]):
                raise ValueError("URL contains a malformed percent escape")
            byte = int(value[index + 1 : index + 3], 16)
            if byte in UNRESERVED_URL_BYTES:
                result.append(chr(byte))
            else:
                result.append(f"%{byte:02X}")
            index += 3
            continue
        encoded = value[index].encode("utf-8")
        for byte in encoded:
            result.append(chr(byte) if byte in safe_bytes else f"%{byte:02X}")
        index += 1
    return "".join(result)


def _remove_dot_segments(path: str) -> str:
    """Apply RFC 3986 section 5.2.4 while preserving empty/trailing segments."""
    remaining = path
    output = ""
    while remaining:
        if remaining.startswith("../"):
            remaining = remaining[3:]
        elif remaining.startswith("./"):
            remaining = remaining[2:]
        elif remaining.startswith("/./"):
            remaining = "/" + remaining[3:]
        elif remaining == "/.":
            remaining = "/"
        elif remaining.startswith("/../"):
            remaining = "/" + remaining[4:]
            output = output.rsplit("/", 1)[0]
        elif remaining == "/..":
            remaining = "/"
            output = output.rsplit("/", 1)[0]
        elif remaining in {".", ".."}:
            remaining = ""
        else:
            next_slash = remaining.find("/", 1 if remaining.startswith("/") else 0)
            if next_slash == -1:
                output += remaining
                remaining = ""
            else:
                output += remaining[:next_slash]
                remaining = remaining[next_slash:]
    return output or "/"


def _percent_decode_layers(value: str, rounds: int = 3) -> list[str]:
    layers = [value]
    for _ in range(rounds):
        decoded = unquote(layers[-1])
        if decoded == layers[-1]:
            break
        layers.append(decoded)
    return layers


def _security_parameter_pairs(component: str) -> list[tuple[str, str]]:
    """Parse security-sensitive separators without changing canonical bytes."""
    pairs: list[tuple[str, str]] = []
    for layer in _percent_decode_layers(component.lstrip("?#")):
        for segment in re.split(r"[&;]", layer):
            if segment:
                pairs.extend(parse_qsl(segment, keep_blank_values=True))
    return pairs


SENSITIVE_COMPACT_KEY_SUFFIXES = (
    "accesstoken",
    "apikey",
    "accesskeyid",
    "authorization",
    "credential",
    "credentials",
    "idtoken",
    "jsessionid",
    "jwt",
    "oauthtoken",
    "password",
    "passwd",
    "phpsessid",
    "privatekey",
    "privatetoken",
    "refreshtoken",
    "samlresponse",
    "secret",
    "secretaccesskey",
    "secretkey",
    "session",
    "sessionid",
    "sharedaccesssignature",
    "signature",
    "signingkey",
    "keypairid",
    "token",
)


def _sensitive_parameter_key(key: str) -> tuple[bool, str]:
    decoded_key = _percent_decode_layers(key)[-1]
    normalized_key = re.sub(r"[^a-z0-9]+", "_", normalized_label(decoded_key)).strip("_")
    compact_key = re.sub(r"[^a-z0-9]+", "", normalized_label(decoded_key))
    sensitive_suffix = normalized_key.endswith(("_password", "_secret", "_signature", "_token"))
    key_components = set(normalized_key.split("_"))
    structured_sensitive = "_" in normalized_key and bool(
        key_components
        & {"auth", "authorization", "credential", "credentials", "jwt", "passwd", "password", "secret", "session", "token"}
    )
    sensitive = (
        normalized_key in SENSITIVE_QUERY_KEYS
        or normalized_key.startswith(("x_amz_", "x_goog_"))
        or sensitive_suffix
        or structured_sensitive
        or compact_key.endswith(SENSITIVE_COMPACT_KEY_SUFFIXES)
    )
    return sensitive, decoded_key


def _reject_sensitive_url_material(value: str, parameter_pairs: list[tuple[str, str]], fragment: str) -> None:
    decoded = _percent_decode_layers(value)[-1]
    if EMAIL_RE.search(decoded):
        raise ValueError("URL contains an email address")
    fragment_pairs = _security_parameter_pairs(fragment)
    for key, item in [*parameter_pairs, *fragment_pairs]:
        # Treat bracketed/nested query syntaxes as separators too. Frameworks
        # commonly decode token[], auth[token], and token.foo into credential
        # fields even though their raw spellings evade exact-key checks.
        sensitive_key, decoded_key = _sensitive_parameter_key(key)
        if sensitive_key:
            raise ValueError("URL parameter may contain a credential or session secret")
        normalized_key = re.sub(r"[^a-z0-9]+", "_", normalized_label(decoded_key)).strip("_")
        decoded_item = _percent_decode_layers(item)[-1]
        phone_key = normalized_key in {"contact", "mobile", "phone", "telephone", "tel"}
        phone_shape = PHONE_RE.search(decoded_item) and (
            phone_key or "+" in decoded_item or "(" in decoded_item or ")" in decoded_item or " " in decoded_item
        )
        if EMAIL_RE.search(decoded_item) or phone_shape:
            raise ValueError("URL contains personal data in a parameter")
    decoded_fragment = _percent_decode_layers(fragment)[-1]
    if EMAIL_RE.search(decoded_fragment) or (PHONE_RE.search(decoded_fragment) and "+" in decoded_fragment):
        raise ValueError("URL fragment contains personal data")


def _canonical_base36_identifier(value: str, label: str) -> str:
    """Return one positive Reddit thing ID spelling, rejecting lookalikes."""
    if not re.fullmatch(r"[0-9A-Za-z]+", value):
        raise ValueError(f"Reddit {label} must be an ASCII base-36 identifier")
    if len(value) > MAX_REDDIT_ID_CHARS:
        raise ValueError(f"Reddit {label} exceeds {MAX_REDDIT_ID_CHARS} base-36 characters")
    number = int(value, 36)
    if number <= 0:
        raise ValueError(f"Reddit {label} must be a positive base-36 identifier")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while number:
        number, remainder = divmod(number, 36)
        encoded = alphabet[remainder] + encoded
    return encoded


def _canonical_decimal_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{label} requires an ASCII numeric id")
    if len(value) > MAX_NATIVE_DECIMAL_ID_CHARS:
        raise ValueError(f"{label} id exceeds {MAX_NATIVE_DECIMAL_ID_CHARS} digits")
    number = int(value)
    if number <= 0:
        raise ValueError(f"{label} requires a positive numeric id")
    return str(number)


def canonicalize_url(value: str) -> str:
    if DISALLOWED_CONTROL_RE.search(value) or "\r" in value or "\n" in value or "\t" in value:
        raise ValueError("URL contains control characters")
    if "\\" in value:
        raise ValueError("URL must use forward slashes, not browser-dependent backslashes")
    parts = urlsplit(strip_pinned_whitespace(value))
    scheme = ascii_casefold(parts.scheme)
    raw_host = parts.hostname or ""
    authority = parts.netloc.rsplit("@", 1)[-1]
    host_was_bracketed = authority.startswith("[")
    if "%" in raw_host:
        raise ValueError("URL host must not contain percent escapes")
    try:
        raw_host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("URL host must use an ASCII IDNA A-label, not raw Unicode") from exc
    host = ascii_casefold(raw_host.rstrip("."))
    if not scheme or not host:
        raise ValueError("URL requires a scheme and host")
    try:
        parsed_address = ipaddress.ip_address(host)
    except ValueError:
        parsed_address = None
    if host_was_bracketed and (parsed_address is None or parsed_address.version != 6):
        raise ValueError("Bracketed URL hosts must be supported IPv6 literals")
    if parsed_address is not None:
        # One compressed spelling prevents IPv6 aliases from manufacturing
        # distinct hosts, platform families, threads, or source units.
        host = ascii_casefold(parsed_address.compressed)
    if ":" not in host and (
        len(host) > 253
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        )
    ):
        raise ValueError("URL host contains an invalid DNS label")
    if scheme not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL credentials are not allowed")
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        port_text = authority[closing_bracket + 2 :] if authority[closing_bracket + 1 :].startswith(":") else ""
    else:
        port_text = authority.rsplit(":", 1)[1] if ":" in authority else ""
    if len(port_text) > MAX_PORT_CHARS:
        raise ValueError(f"URL port exceeds {MAX_PORT_CHARS} characters")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    if port == 0:
        raise ValueError("URL port must be between 1 and 65535")
    host_for_netloc = f"[{host}]" if ":" in host else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host_for_netloc}:{port}"
    else:
        netloc = host_for_netloc
    raw_query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    security_pairs = _security_parameter_pairs(parts.query)
    for path_layer in _percent_decode_layers(parts.path):
        for path_segment in path_layer.split("/"):
            if ";" in path_segment:
                security_pairs.extend(_security_parameter_pairs(path_segment.split(";", 1)[1]))
    _reject_sensitive_url_material(value, security_pairs, parts.fragment or "")
    path = _remove_dot_segments(_normalize_percent_component(parts.path or "/", PATH_SAFE_URL_BYTES))
    fragment = ""
    query_pairs = [(key, val) for key, val in raw_query_pairs if normalized_label(key) not in TRACKING_PARAMS]
    query = ""

    native_rewrite_host = (
        host in {"reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "www.reddit.com"}
        or host == "github.com"
        or host in {"news.ycombinator.com", "www.news.ycombinator.com"}
    )
    if native_rewrite_host and port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        raise ValueError("Native community URL uses a nondefault port and cannot be rewritten to an official origin")

    if host in {"reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "www.reddit.com"}:
        scheme = "https"
        netloc = "www.reddit.com"
        path = re.sub(r"/{2,}", "/", path)
        path = re.sub(r"^/[a-z]{2}(?:-[A-Z]{2})?(?=/r/)", "", path)
        reddit_parts = [part for part in path.split("/") if part]
        if (
            len(reddit_parts) >= 4
            and ascii_casefold(reddit_parts[0]) == "r"
            and ascii_casefold(reddit_parts[2]) == "comments"
        ):
            reddit_parts[0] = "r"
            reddit_parts[1] = ascii_casefold(reddit_parts[1])
            reddit_parts[2] = "comments"
            reddit_parts[3] = _canonical_base36_identifier(reddit_parts[3], "post id")
            if len(reddit_parts) >= 6:
                reddit_parts[5] = _canonical_base36_identifier(reddit_parts[5], "comment id")
            path = "/" + "/".join(reddit_parts)
        path = path.rstrip("/") + "/"
    elif host == "github.com":
        scheme = "https"
        netloc = host
        path = re.sub(r"/{2,}", "/", path)
        github_parts = [part for part in path.split("/") if part]
        if (
            len(github_parts) == 4
            and ascii_casefold(github_parts[2]) in {"issues", "discussions"}
            and re.fullmatch(r"[0-9]+", github_parts[3])
        ):
            github_parts[3] = _canonical_decimal_identifier(
                github_parts[3], "GitHub issue or discussion URL"
            )
            path = "/" + "/".join(github_parts)
        query_pairs = [
            (key, val)
            for key, val in query_pairs
            if ascii_casefold(key) not in {"notification_referrer_id"}
        ]
        query = urlencode(query_pairs, doseq=True)
        if re.fullmatch(r"(?:issuecomment|discussioncomment|discussion_r)-[0-9]+", parts.fragment or ""):
            fragment = parts.fragment
        path = path.rstrip("/") or "/"
    elif host in {"news.ycombinator.com", "www.news.ycombinator.com"}:
        scheme = "https"
        netloc = "news.ycombinator.com"
        path = re.sub(r"/{2,}", "/", path)
        if path.rstrip("/") == "/item":
            item_values = [val for key, val in query_pairs if key == "id"]
            if len(item_values) != 1:
                raise ValueError("Hacker News item URL requires exactly one positive ASCII numeric id")
            try:
                canonical_item_id = _canonical_decimal_identifier(item_values[0], "Hacker News item URL")
            except ValueError as exc:
                raise ValueError("Hacker News item URL requires exactly one positive ASCII numeric id") from exc
            query = urlencode([("id", canonical_item_id)])
            path = "/item"
        path = path.rstrip("/") or "/"
    else:
        query = _normalize_percent_component(parts.query, FRAGMENT_SAFE_URL_BYTES)
        fragment = _normalize_percent_component(parts.fragment, FRAGMENT_SAFE_URL_BYTES)

    return urlunsplit((scheme, netloc, path, query, fragment))


@dataclass(frozen=True)
class PublicIdentity:
    unit_id: str
    thread_id: str
    thread_key: str


def _positive_item_id(canonical_url: str) -> str | None:
    parts = urlsplit(canonical_url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if parts.path != "/item" or len(pairs) != 1 or pairs[0][0] != "id":
        return None
    value = pairs[0][1]
    return value if re.fullmatch(r"[0-9]+", value) and int(value) > 0 else None


def derive_public_identity(
    platform: str,
    canonical_url: str,
    canonical_thread_url: str,
    source_type: str,
) -> PublicIdentity:
    platform = canonical_platform(platform)
    unit_parts = urlsplit(canonical_url)
    thread_parts = urlsplit(canonical_thread_url)
    unit_host = ascii_casefold(unit_parts.hostname or "")
    thread_host = ascii_casefold(thread_parts.hostname or "")
    if unit_host != thread_host or unit_parts.port != thread_parts.port:
        raise PublicIdentityError(
            "THREAD_HOST_MISMATCH",
            "thread_url",
            "Source URL and thread URL must use the same canonical host and explicit port.",
        )

    if platform == "reddit":
        unit_path = [part for part in unit_parts.path.split("/") if part]
        thread_path = [part for part in thread_parts.path.split("/") if part]
        valid_unit = (
            len(unit_path) >= 4
            and ascii_casefold(unit_path[0]) == "r"
            and ascii_casefold(unit_path[2]) == "comments"
        )
        valid_thread = (
            4 <= len(thread_path) <= 5
            and ascii_casefold(thread_path[0]) == "r"
            and ascii_casefold(thread_path[2]) == "comments"
        )
        if not valid_thread:
            raise PublicIdentityError(
                "THREAD_PERMALINK",
                "thread_url",
                "Reddit thread_url must be a direct post permalink, not a comment permalink.",
            )
        if not valid_unit:
            raise PublicIdentityError("THREAD_PERMALINK", "url", "Reddit URL must be a direct post or comment permalink.")
        if (
            ascii_casefold(unit_path[1]) != ascii_casefold(thread_path[1])
            or ascii_casefold(unit_path[3]) != ascii_casefold(thread_path[3])
        ):
            raise PublicIdentityError(
                "THREAD_URL_MISMATCH",
                "thread_url",
                "Source URL and thread_url refer to different Reddit posts.",
            )
        thread_id = f"reddit:t3_{ascii_casefold(thread_path[3])}"
        if source_type == "post":
            if len(unit_path) > 5:
                raise PublicIdentityError("SOURCE_TYPE_MISMATCH", "source_type", "Reddit post sources cannot use a comment permalink.")
            unit_id = thread_id
        elif source_type == "comment":
            if len(unit_path) != 6:
                raise PublicIdentityError(
                    "COMMENT_PERMALINK",
                    "url",
                    "Reddit comment sources require an exact direct comment permalink with no trailing path components.",
                )
            unit_id = f"reddit:t1_{ascii_casefold(unit_path[5])}"
        else:
            raise PublicIdentityError("SOURCE_TYPE_MISMATCH", "source_type", "Reddit public sources must be post or comment records.")
        return PublicIdentity(unit_id, thread_id, thread_id)

    if platform == "github":
        unit_path = [part for part in unit_parts.path.split("/") if part]
        thread_path = [part for part in thread_parts.path.split("/") if part]
        valid_thread = (
            len(thread_path) == 4
            and ascii_casefold(thread_path[2]) in {"issues", "discussions"}
            and re.fullmatch(r"[0-9]+", thread_path[3])
            and int(thread_path[3]) > 0
            and not thread_parts.query
            and not thread_parts.fragment
        )
        if not valid_thread:
            raise PublicIdentityError(
                "THREAD_PERMALINK",
                "thread_url",
                "GitHub thread_url must be a root /owner/repo/issues/<n> or /owner/repo/discussions/<n> URL.",
            )
        if (
            len(unit_path) != 4
            or [ascii_casefold(part) for part in unit_path] != [ascii_casefold(part) for part in thread_path]
            or unit_parts.query
        ):
            raise PublicIdentityError(
                "THREAD_URL_MISMATCH",
                "thread_url",
                "GitHub source URL and thread_url must refer to the same issue or discussion.",
            )
        owner, repo, collection, number = [ascii_casefold(part) for part in thread_path]
        number = str(int(number))
        kind = "issue" if collection == "issues" else "discussion"
        thread_id = f"github:{owner}/{repo}:{kind}:{number}"
        fragment = ascii_casefold(unit_parts.fragment)
        if source_type == "comment":
            expected_fragment = r"issuecomment-[1-9][0-9]*" if kind == "issue" else r"discussioncomment-[1-9][0-9]*"
            if not re.fullmatch(expected_fragment, fragment):
                raise PublicIdentityError(
                    "COMMENT_PERMALINK",
                    "url",
                    f"GitHub {kind} comments require their direct comment fragment.",
                )
            unit_id = f"{thread_id}#{fragment}"
        elif (kind == "issue" and source_type == "issue") or (kind == "discussion" and source_type == "discussion"):
            if fragment:
                raise PublicIdentityError("SOURCE_TYPE_MISMATCH", "source_type", "GitHub root records cannot use a comment fragment.")
            unit_id = thread_id
        else:
            raise PublicIdentityError(
                "SOURCE_TYPE_MISMATCH",
                "source_type",
                f"GitHub {kind} URLs require source_type {kind!r} or 'comment'.",
            )
        return PublicIdentity(unit_id, thread_id, thread_id)

    if platform == "hackernews":
        unit_item = _positive_item_id(canonical_url)
        thread_item = _positive_item_id(canonical_thread_url)
        if thread_item is None:
            raise PublicIdentityError(
                "THREAD_PERMALINK",
                "thread_url",
                "Hacker News thread_url must be an item URL with one positive numeric id.",
            )
        if unit_item is None:
            raise PublicIdentityError("THREAD_PERMALINK", "url", "Hacker News URL must be an item URL with one positive numeric id.")
        thread_id = f"hackernews:item:{thread_item}"
        unit_id = f"hackernews:item:{unit_item}"
        if source_type == "story" and unit_item != thread_item:
            raise PublicIdentityError("THREAD_URL_MISMATCH", "thread_url", "Hacker News story URL must equal its thread_url.")
        if source_type == "comment" and unit_item == thread_item:
            raise PublicIdentityError("COMMENT_THREAD_SELF", "url", "Hacker News comment URL must identify a different item from its root thread_url.")
        if source_type not in {"story", "comment"}:
            raise PublicIdentityError("SOURCE_TYPE_MISMATCH", "source_type", "Hacker News public sources must be story or comment records.")
        return PublicIdentity(unit_id, thread_id, thread_id)

    return PublicIdentity(canonical_url, canonical_thread_url, "url:" + canonical_thread_url)


def thread_identity_key(source: dict[str, Any]) -> str:
    if source.get("visibility") == "public":
        try:
            canonical_url = canonicalize_url(str(source.get("url", "")))
            canonical_thread_url = canonicalize_url(str(source.get("thread_url", "")))
            return derive_public_identity(
                str(source.get("platform", "")),
                canonical_url,
                canonical_thread_url,
                str(source.get("source_type", "")),
            ).thread_key
        except ValueError:
            try:
                return "url:" + canonicalize_url(str(source.get("thread_url", "")))
            except ValueError:
                return "invalid-public-thread"
    return "private:" + canonical_platform(source.get("platform", "")) + ":" + normalized_label(source.get("thread_id", ""))


def derived_community(platform: str, url: str) -> str | None:
    try:
        canonical = canonicalize_url(url)
    except ValueError:
        return None
    parts = urlsplit(canonical)
    path_parts = [part for part in parts.path.split("/") if part]
    platform = canonical_platform(platform)
    if platform == "reddit" and len(path_parts) >= 2 and ascii_casefold(path_parts[0]) == "r":
        return "r/" + path_parts[1]
    if platform == "github" and len(path_parts) >= 2 and parts.hostname == "github.com":
        return path_parts[0] + "/" + path_parts[1]
    if platform == "hackernews" and parts.hostname == "news.ycombinator.com":
        return "news.ycombinator.com"
    return None


def source_platform_identity(source: dict[str, Any]) -> str:
    """Return a non-gameable platform family for diversity accounting."""
    if source.get("visibility") == "supplied_private":
        return "export"
    try:
        canonical = canonicalize_url(str(source.get("url", "")))
        parts = urlsplit(canonical)
    except ValueError:
        return "invalid-public-platform"
    host = ascii_casefold(parts.hostname or "")
    if host == "www.reddit.com":
        return "reddit"
    if host == "github.com":
        return "github"
    if host == "news.ycombinator.com":
        return "hackernews"
    return f"web:{host}"


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
    if len(value) > MAX_OBJECT_FIELDS:
        audit.error("OBJECT_TOO_LARGE", path, f"Object exceeds {MAX_OBJECT_FIELDS} fields.")
    allowed = required if allowed is None else allowed
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - allowed)[:MAX_OBJECT_FIELDS]
    for key in missing:
        audit.error("MISSING_FIELD", f"{path}.{key}", "Required field is missing.")
    for key in unknown:
        audit.error("UNKNOWN_FIELD", f"{path}.{key}", "Unknown field; fix misspellings or remove it.")
    return value


def check_string(value: Any, path: str, audit: Audit, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        audit.error("TYPE", path, "Expected a string.")
        return ""
    # Enforce the lexical bound before Unicode normalization, regular
    # expressions, URL parsing, or any other semantic work.
    if len(value) > MAX_GENERAL_STRING:
        audit.error("STRING_TOO_LARGE", path, f"String exceeds {MAX_GENERAL_STRING} characters.")
        return ""
    if not allow_empty and not strip_pinned_whitespace(value):
        audit.error("EMPTY_STRING", path, "Value must not be empty.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        audit.error("UNICODE", path, "String contains an invalid Unicode surrogate.")
        return ""
    if DISALLOWED_CONTROL_RE.search(value):
        audit.error("CONTROL_CHARACTER", path, "String contains a disallowed C0 or C1 control character.")
        return ""
    if BIDI_FORMAT_RE.search(value):
        audit.error("BIDI_CONTROL", path, "String contains a bidirectional formatting control.")
        return ""
    return value


def bounded_list(value: Any, path: str, audit: Audit) -> list[Any]:
    if not isinstance(value, list):
        audit.error("TYPE", path, "Expected a list.")
        return []
    if len(value) > MAX_LIST_ITEMS:
        audit.error("LIST_TOO_LARGE", path, f"List exceeds {MAX_LIST_ITEMS} items.")
    return value[:MAX_LIST_ITEMS]


def check_string_list(value: Any, path: str, audit: Audit, *, allow_empty: bool = True) -> list[str]:
    items = bounded_list(value, path, audit)
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not strip_pinned_whitespace(item):
            audit.error("TYPE", f"{path}[{index}]", "Expected a non-empty string.")
        else:
            checked = check_string(item, f"{path}[{index}]", audit)
            if checked:
                result.append(checked)
    if not allow_empty and not result:
        audit.error("EMPTY_LIST", path, "At least one value is required.")
    if len(result) != len(set(result)):
        audit.error("DUPLICATE_LIST_VALUE", path, "List values must be unique.")
    return result


def check_enum_string(value: Any, path: str, allowed: Iterable[str], audit: Audit) -> str:
    checked = check_string(value, path, audit)
    choices = set(allowed)
    if checked and checked not in choices:
        audit.error("ENUM", path, f"Expected one of {sorted(choices)}.")
    return checked


def check_nonnegative_int(value: Any, path: str, audit: Audit) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        audit.error("TYPE", path, "Expected a non-negative integer.")
        return 0
    return value


def is_link_or_reparse(path: Path) -> bool:
    """Return whether *path* is a symlink, junction, or other Windows reparse point.

    ``Path.is_symlink()`` does not identify directory junctions on Windows.  The
    ``is_junction`` method was added after our Python 3.10 minimum, so retain an
    ``lstat`` attribute fallback for older supported interpreters and for other
    reparse-point types.
    """
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


class StudyBuildLock:
    """Hold the exclusive, cross-process writer lock for one study.

    The lock file is deliberately persistent. Removing it on release would
    allow a waiter to open the old inode while a new writer locks a newly
    created inode at the same path, defeating mutual exclusion.
    """

    def __init__(self, study_dir: Path) -> None:
        self.study_dir = study_dir
        self.path = study_dir / ".csr-build.lock"
        self._fd: int | None = None

    def __enter__(self) -> "StudyBuildLock":
        if is_link_or_reparse(self.study_dir) or not self.study_dir.is_dir():
            raise ValueError("Refusing a build lock through a linked, reparse-point, or missing study directory")
        if is_link_or_reparse(self.path):
            raise ValueError("Refusing a linked or reparse-point study build lock")

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise ValueError("Unable to open the study build lock safely") from exc
        self._fd = fd
        try:
            descriptor_stat = os.fstat(fd)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise ValueError("Study build lock must be a regular file")
            if is_link_or_reparse(self.path):
                raise ValueError("Refusing a linked or reparse-point study build lock")
            path_stat = self.path.stat()
            if not os.path.samestat(descriptor_stat, path_stat):
                raise ValueError("Study build lock changed while it was being opened")
            if not self.path.resolve().is_relative_to(self.study_dir.resolve()):
                raise ValueError("Study build lock must remain inside the study directory")
            if descriptor_stat.st_size > 1:
                raise ValueError("Study build lock has unexpected content; inspect it before retrying")
            if descriptor_stat.st_size == 0:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b"\0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("Another build or recovery is active for this study") from None

            # Recheck the stable pathname after acquiring the descriptor lock.
            if is_link_or_reparse(self.path) or not os.path.samestat(os.fstat(fd), self.path.stat()):
                raise ValueError("Study build lock changed while it was being acquired")
            return self
        except BaseException:
            os.close(fd)
            self._fd = None
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            # Closing the descriptor also releases the advisory lock.
            pass
        finally:
            os.close(fd)


def read_regular_file_bounded(path: Path, max_bytes: int, label: str) -> bytes:
    """Read one stable regular file through a bounded, identity-checked descriptor."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    if is_link_or_reparse(path.parent):
        raise UnsafeFileReadError(f"{label} parent is a link, junction, or reparse point")
    if is_link_or_reparse(path):
        raise UnsafeFileReadError(f"{label} is a link, junction, or reparse point")
    try:
        root = path.parent.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise
    if not resolved.is_relative_to(root):
        raise UnsafeFileReadError(f"{label} escapes its declared parent directory")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise UnsafeFileReadError(f"Unable to open {label} safely") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise UnsafeFileReadError(f"{label} is not a regular file")

        def recheck_identity() -> os.stat_result:
            if is_link_or_reparse(path.parent) or is_link_or_reparse(path):
                raise UnsafeFileReadError(f"{label} changed to a link, junction, or reparse point")
            try:
                current_root = path.parent.resolve(strict=True)
                current_resolved = path.resolve(strict=True)
                path_stat = path.stat()
            except FileNotFoundError as exc:
                raise UnsafeFileReadError(f"{label} changed while it was being read") from exc
            if current_root != root or current_resolved != resolved or not current_resolved.is_relative_to(current_root):
                raise UnsafeFileReadError(f"{label} changed location while it was being read")
            descriptor_stat = os.fstat(fd)
            if not os.path.samestat(descriptor_stat, path_stat):
                raise UnsafeFileReadError(f"{label} changed identity while it was being read")
            return descriptor_stat

        before = recheck_identity()
        if before.st_size > max_bytes:
            raise BoundedFileTooLargeError(f"{label} exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = recheck_identity()
        if len(payload) > max_bytes or after.st_size > max_bytes:
            raise BoundedFileTooLargeError(f"{label} exceeds {max_bytes} bytes")
        stable_fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise UnsafeFileReadError(f"{label} changed while it was being read")
        return payload
    finally:
        os.close(fd)


def read_input_text(path: Path, audit: Audit) -> str | None:
    try:
        payload = read_regular_file_bounded(path, MAX_FILE_BYTES, "Input")
    except FileNotFoundError:
        audit.error("MISSING_FILE", path.name, "Required input file is missing.")
        return None
    except BoundedFileTooLargeError:
        audit.error("FILE_TOO_LARGE", path.name, f"Input exceeds {MAX_FILE_BYTES} bytes.")
        return None
    except (UnsafeFileReadError, OSError):
        audit.error(
            "UNSAFE_INPUT_PATH",
            path.name,
            "Input must remain one regular file inside the real study directory while it is read.",
        )
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        audit.error("ENCODING", path.name, "File must be UTF-8 encoded.")
        return None


def load_json(path: Path, audit: Audit) -> Any:
    text = read_input_text(path, audit)
    if text is None:
        return {}
    try:
        return strict_json_loads(text)
    except JsonDepthError as exc:
        audit.error("JSON_DEPTH", path.name, str(exc) + ".")
    except json.JSONDecodeError as exc:
        audit.error("JSON", path.name, f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}.")
    except (DuplicateKeyError, ValueError) as exc:
        audit.error("JSON", path.name, str(exc) + ".")
    return {}


def load_jsonl(path: Path, audit: Audit) -> list[Any]:
    text = read_input_text(path, audit)
    if text is None:
        return []
    records: list[Any] = []
    physical_line_limit = MAX_RECORDS + MAX_JSONL_EXTRA_LINES
    for line_number, line in enumerate(io.StringIO(text), start=1):
        if line_number > physical_line_limit:
            audit.error(
                "JSONL_LINE_LIMIT",
                path.name,
                f"Input exceeds {physical_line_limit} physical lines, including blank or malformed rows.",
            )
            break
        if not strip_pinned_whitespace(line):
            continue
        try:
            record = strict_json_loads(line)
        except JsonDepthError as exc:
            audit.error("JSON_DEPTH", f"{path.name}:{line_number}", str(exc) + ".")
        except json.JSONDecodeError as exc:
            audit.error("JSONL", f"{path.name}:{line_number}", f"Invalid JSON: {exc.msg}.")
        except (DuplicateKeyError, ValueError) as exc:
            audit.error("JSONL", f"{path.name}:{line_number}", str(exc) + ".")
        else:
            if len(records) >= MAX_RECORDS:
                audit.error("TOO_MANY_RECORDS", path.name, f"Input exceeds {MAX_RECORDS} records.")
                break
            records.append(record)
        if audit.issue_limit_reached:
            break
    return records


def validate_schema_version(record: dict[str, Any], path: str, audit: Audit) -> None:
    version = check_string(record.get("schema_version"), f"{path}.schema_version", audit)
    record["schema_version"] = version
    if version and version != SCHEMA_VERSION:
        audit.error("SCHEMA_VERSION", f"{path}.schema_version", f"Expected {SCHEMA_VERSION!r}.")


def validate_plan(raw: Any, audit: Audit) -> dict[str, Any]:
    plan = check_object(raw, "study-plan.json", PLAN_REQUIRED, audit)
    if not plan:
        return {}
    validate_schema_version(plan, "study-plan.json", audit)
    study_id = check_string(plan.get("study_id"), "study-plan.json.study_id", audit)
    plan["study_id"] = study_id
    if study_id and not STUDY_ID_RE.fullmatch(study_id):
        audit.error("STUDY_ID", "study-plan.json.study_id", "Use 3-80 lowercase letters, digits, dots, underscores, or hyphens.")
    for key in ("question", "decision", "population", "stop_condition"):
        plan[key] = check_string(plan.get(key), f"study-plan.json.{key}", audit)
    plan["mode"] = check_enum_string(plan.get("mode"), "study-plan.json.mode", MODES, audit)
    as_of = parse_date(plan.get("as_of"), "study-plan.json.as_of", audit)
    if as_of is None and isinstance(plan.get("as_of"), str):
        plan["as_of"] = ""
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
    if start_date is None and isinstance(date_window.get("start"), str):
        date_window["start"] = ""
    if end_date is None and isinstance(date_window.get("end"), str):
        date_window["end"] = ""
    if start_date and end_date and start_date > end_date:
        audit.error("DATE_WINDOW", "study-plan.json.date_window", "Start date cannot be later than end date.")
    if end_date and as_of and end_date > as_of:
        audit.error("DATE_WINDOW", "study-plan.json.date_window.end", "Date-window end cannot be later than as_of.")

    scope = check_object(plan.get("scope"), "study-plan.json.scope", {"platforms", "communities", "languages"}, audit)
    for key in ("platforms", "communities", "languages"):
        values = check_string_list(scope.get(key), f"study-plan.json.scope.{key}", audit, allow_empty=False)
        scope[key] = values
        normalized_values = [canonical_platform(value) if key == "platforms" else normalized_label(value) for value in values]
        if len(set(normalized_values)) != len(values):
            audit.error("DUPLICATE_NORMALIZED_VALUE", f"study-plan.json.scope.{key}", "Values must also be unique after Unicode/case and platform-alias normalization.")
    plan["inclusion_criteria"] = check_string_list(plan.get("inclusion_criteria"), "study-plan.json.inclusion_criteria", audit, allow_empty=False)
    plan["exclusion_criteria"] = check_string_list(plan.get("exclusion_criteria"), "study-plan.json.exclusion_criteria", audit, allow_empty=False)
    plan["limitations"] = check_string_list(plan.get("limitations"), "study-plan.json.limitations", audit)

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
    plan["counterevidence_status"] = check_enum_string(
        plan.get("counterevidence_status"),
        "study-plan.json.counterevidence_status",
        COUNTER_STATUSES,
        audit,
    )
    return plan


def validate_queries(
    raw_records: list[Any], plan: dict[str, Any], audit: Audit
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    execution_owner: dict[tuple[str, str, str, str], str] = {}
    scope = plan.get("scope", {}) if isinstance(plan.get("scope"), dict) else {}
    allowed_platforms = {canonical_platform(value) for value in scope.get("platforms", [])}
    for index, raw in enumerate(raw_records):
        path = f"query-log.jsonl[{index}]"
        record = check_object(raw, path, QUERY_REQUIRED, audit)
        if not record:
            continue
        validate_schema_version(record, path, audit)
        query_id = check_string(record.get("id"), f"{path}.id", audit)
        record["id"] = query_id
        if query_id and not ID_RE.fullmatch(query_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif query_id and not re.fullmatch(r"qry[-_][a-z0-9][a-z0-9_-]*", query_id):
            audit.error("ID_PREFIX", f"{path}.id", "Query IDs must start with qry- or qry_.")
        if query_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate query ID {query_id!r}.")
        platform = check_string(record.get("platform"), f"{path}.platform", audit)
        record["platform"] = platform
        platform_key = canonical_platform(platform)
        if allowed_platforms and platform_key not in allowed_platforms:
            audit.error("PLATFORM_SCOPE", f"{path}.platform", "Query platform is outside the declared scope.")
        query_text = check_string(record.get("query"), f"{path}.query", audit)
        record["query"] = query_text
        record["intent"] = check_enum_string(record.get("intent"), f"{path}.intent", QUERY_INTENTS, audit)
        run_at = parse_datetime(record.get("run_at"), f"{path}.run_at", audit)
        if run_at is None and isinstance(record.get("run_at"), str):
            record["run_at"] = ""
        sort_text = check_string(record.get("sort"), f"{path}.sort", audit)
        record["sort"] = sort_text
        if run_at is not None and record.get("intent") in QUERY_INTENTS:
            execution_key = (
                platform_key,
                normalized_label(query_text),
                run_at.isoformat(),
                normalized_label(sort_text),
            )
            if execution_key in execution_owner:
                audit.error(
                    "DUPLICATE_QUERY_EXECUTION",
                    path,
                    f"Duplicates the normalized execution recorded as {execution_owner[execution_key]!r}; record one row per actual query run.",
                )
            else:
                execution_owner[execution_key] = query_id
        seen = check_nonnegative_int(record.get("results_seen"), f"{path}.results_seen", audit)
        screened = check_nonnegative_int(record.get("results_screened"), f"{path}.results_screened", audit)
        check_nonnegative_int(record.get("pages_seen"), f"{path}.pages_seen", audit)
        if isinstance(record.get("pages_seen"), int) and record.get("pages_seen") < 1:
            audit.error("PAGES_SEEN", f"{path}.pages_seen", "Every logged query execution must record at least one results page, including a zero-result page.")
        if screened > seen:
            audit.error("SCREENED_GT_SEEN", f"{path}.results_screened", "Screened results cannot exceed seen results.")
        if seen > 0 and screened == 0:
            audit.error("UNSCREENED_QUERY", f"{path}.results_screened", "A query that returned results must screen at least one result.")
        if not isinstance(record.get("truncated"), bool):
            audit.error("TYPE", f"{path}.truncated", "Expected a boolean.")
        included_source_ids = check_string_list(record.get("included_source_ids"), f"{path}.included_source_ids", audit)
        record["included_source_ids"] = included_source_ids
        if included_source_ids and (seen == 0 or screened == 0):
            audit.error(
                "QUERY_COUNT_MISMATCH",
                f"{path}.included_source_ids",
                "A query with included sources must report at least one seen and screened result.",
            )
        record["signal_ids"] = check_string_list(record.get("signal_ids"), f"{path}.signal_ids", audit)
        record["notes"] = check_string(record.get("notes"), f"{path}.notes", audit, allow_empty=True)
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
    allowed_platforms = {canonical_platform(value) for value in scope.get("platforms", [])}
    allowed_communities = {normalized_label(value) for value in scope.get("communities", [])}
    allowed_languages = {normalized_label(value) for value in scope.get("languages", [])}
    for index, raw in enumerate(raw_records):
        path = f"source-ledger.jsonl[{index}]"
        record = check_object(raw, path, SOURCE_REQUIRED, audit)
        if not record:
            continue
        validate_schema_version(record, path, audit)
        source_id = check_string(record.get("id"), f"{path}.id", audit)
        record["id"] = source_id
        if source_id and not ID_RE.fullmatch(source_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif source_id and not re.fullmatch(r"src[-_][a-z0-9][a-z0-9_-]*", source_id):
            audit.error("ID_PREFIX", f"{path}.id", "Source IDs must start with src- or src_.")
        if source_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate source ID {source_id!r}.")
        for key in ("platform", "community", "unit_id", "thread_id", "language", "excerpt"):
            record[key] = check_string(record.get(key), f"{path}.{key}", audit)
        record["title"] = check_string(record.get("title"), f"{path}.title", audit, allow_empty=True)
        platform = canonical_platform(record.get("platform", ""))
        community = normalized_label(record.get("community", ""))
        if allowed_platforms and platform not in allowed_platforms:
            audit.error("PLATFORM_SCOPE", f"{path}.platform", "Source platform is outside the declared scope.")
        if allowed_communities and community not in allowed_communities:
            audit.error("COMMUNITY_SCOPE", f"{path}.community", "Source community is outside the declared scope.")
        visibility = check_enum_string(record.get("visibility"), f"{path}.visibility", VISIBILITY_VALUES, audit)
        record["visibility"] = visibility
        record["capture_method"] = check_enum_string(
            record.get("capture_method"), f"{path}.capture_method", CAPTURE_METHODS, audit
        )
        record["source_status"] = check_enum_string(
            record.get("source_status"), f"{path}.source_status", SOURCE_STATUSES, audit
        )
        record["source_type"] = check_enum_string(
            record.get("source_type"), f"{path}.source_type", SOURCE_TYPES, audit
        )
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
                record[key] = value
                if value:
                    try:
                        canonical = canonicalize_url(value)
                        canonical_urls[key] = canonical
                        if value != canonical:
                            audit.warn("NONCANONICAL_URL", f"{path}.{key}", f"Use canonical URL {canonical!r}.")
                        # Only validated canonical locators may reach fingerprints or
                        # generated artifacts; raw query/fragment material is never rendered.
                        record[key] = canonical
                    except ValueError as exc:
                        audit.error("URL", f"{path}.{key}", str(exc) + ".")
            canonical_url = canonical_urls.get("url", "")
            canonical_thread_url = canonical_urls.get("thread_url", "")
            hosts: dict[str, str] = {}
            for key, canonical in canonical_urls.items():
                host = ascii_casefold(urlsplit(canonical).hostname or "")
                hosts[key] = host
                if host:
                    try:
                        address = ipaddress.ip_address(host.split("%", 1)[0])
                    except ValueError:
                        address = None
                    legacy_numeric_host = address is None and all(
                        re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)", label)
                        for label in host.split(".")
                    )
                    unsafe_host = (
                        host == "localhost"
                        or any(
                            host == suffix.lstrip(".") or host.endswith(suffix)
                            for suffix in NONPUBLIC_HOST_SUFFIXES
                        )
                        or (address is None and "." not in host)
                        or legacy_numeric_host
                    )
                    if address is not None and is_special_use_address(address):
                        unsafe_host = True
                    if unsafe_host:
                        audit.error(
                            "NONPUBLIC_URL",
                            f"{path}.{key}",
                            "Public evidence URLs must use a publicly routable host, not localhost, a private/link-local address, or a single-label intranet host.",
                        )
            host = hosts.get("url", "")
            native_family = {
                "www.reddit.com": "reddit",
                "github.com": "github",
                "news.ycombinator.com": "hackernews",
            }.get(host)
            if native_family and platform != native_family:
                audit.error(
                    "PLATFORM_URL_MISMATCH",
                    f"{path}.platform",
                    f"Canonical host {host!r} requires platform {native_family!r}.",
                )
            expected_hosts = {
                "reddit": {"www.reddit.com"},
                "github": {"github.com"},
                "hackernews": {"news.ycombinator.com"},
            }
            if platform in expected_hosts:
                for key in ("url", "thread_url"):
                    candidate_host = hosts.get(key, "")
                    if candidate_host and candidate_host not in expected_hosts[platform]:
                        audit.error(
                            "PLATFORM_URL_MISMATCH",
                            f"{path}.{key}",
                            f"Platform {platform!r} requires a URL on {sorted(expected_hosts[platform])!r}.",
                        )
            derived = derived_community(platform, canonical_url)
            if derived and normalized_label(derived) != normalized_label(record.get("community", "")):
                audit.error("COMMUNITY_MISMATCH", f"{path}.community", f"Canonical URL implies community {derived!r}.")
            if canonical_url and canonical_thread_url:
                try:
                    identity = derive_public_identity(platform, canonical_url, canonical_thread_url, str(record.get("source_type", "")))
                except PublicIdentityError as exc:
                    audit.error(exc.code, f"{path}.{exc.field}", str(exc))
                else:
                    native_identity = platform in {"reddit", "github", "hackernews"}
                    id_code = "NATIVE_ID_MISMATCH" if native_identity else "PUBLIC_ID_MISMATCH"
                    submitted_thread_id = str(record.get("thread_id", ""))
                    submitted_unit_id = str(record.get("unit_id", ""))
                    thread_matches = (
                        ascii_casefold(submitted_thread_id) == ascii_casefold(identity.thread_id)
                        if native_identity
                        else submitted_thread_id == identity.thread_id
                    )
                    unit_matches = (
                        ascii_casefold(submitted_unit_id) == ascii_casefold(identity.unit_id)
                        if native_identity
                        else submitted_unit_id == identity.unit_id
                    )
                    if not thread_matches:
                        audit.error(id_code, f"{path}.thread_id", f"Canonical thread URL requires thread_id {identity.thread_id!r}.")
                    if not unit_matches:
                        audit.error(id_code, f"{path}.unit_id", f"Canonical unit URL requires unit_id {identity.unit_id!r}.")
        elif visibility == "supplied_private":
            if platform != "export":
                audit.error("PRIVATE_PLATFORM", f"{path}.platform", "Supplied-private records must use platform 'export'.")
            if url is not None or thread_url is not None:
                audit.error("PRIVATE_URL", f"{path}.url", "Supplied-private records must set url and thread_url to null.")
            record_ref = check_string(record_ref, f"{path}.record_ref", audit)
            record["record_ref"] = record_ref
            if record_ref and not OPAQUE_RECORD_REF_RE.fullmatch(record_ref):
                audit.error(
                    "RECORD_REF",
                    f"{path}.record_ref",
                    "Use an opaque 1-256 character reference with letters, digits, dots, underscores, colons, slashes, hashes, or hyphens; do not include personal data.",
                )
            if EMAIL_RE.search(record_ref) or PHONE_RE.search(record_ref):
                audit.error("RECORD_REF_PII", f"{path}.record_ref", "Opaque private provenance must not contain an email address or phone number.")
            file_hash = check_string(file_hash, f"{path}.source_file_sha256", audit)
            record["source_file_sha256"] = file_hash
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", file_hash):
                audit.error("PROVENANCE_HASH", f"{path}.source_file_sha256", "Supplied-private records require sha256:<64 lowercase hex> provenance.")
            if record.get("source_type") != "export_record" or record.get("capture_method") != "export":
                audit.error("EXPORT_CONTRACT", path, "Supplied-private sources require source_type 'export_record' and capture_method 'export'.")
        published = parse_datetime(record.get("published_at"), f"{path}.published_at", audit)
        collected = parse_datetime(record.get("collected_at"), f"{path}.collected_at", audit)
        if published is None and isinstance(record.get("published_at"), str):
            record["published_at"] = ""
        if collected is None and isinstance(record.get("collected_at"), str):
            record["collected_at"] = ""
        if published and collected and published > collected:
            audit.error("TIME_ORDER", f"{path}.published_at", "Publication cannot be later than collection.")
        if published and as_of and published.date() > as_of:
            audit.error("AFTER_AS_OF", f"{path}.published_at", "Publication is later than the study as_of date.")
        if published and start_date and published.date() < start_date:
            audit.error("OUTSIDE_DATE_WINDOW", f"{path}.published_at", "Publication is before the declared date-window start.")
        if published and end_date and published.date() > end_date:
            audit.error("OUTSIDE_DATE_WINDOW", f"{path}.published_at", "Publication is after the declared date-window end.")
        language = normalized_label(record.get("language", ""))
        if allowed_languages and language not in allowed_languages:
            audit.error("LANGUAGE_SCOPE", f"{path}.language", "Source language is outside the declared scope.")
        author_key = check_string(record.get("author_key"), f"{path}.author_key", audit)
        record["author_key"] = author_key
        if author_key != "unknown" and not AUTHOR_KEY_RE.fullmatch(author_key):
            audit.error("AUTHOR_PRIVACY", f"{path}.author_key", "Use 'unknown' or an opaque author: key with 16-64 lowercase hex characters.")
        raw_captured = record.get("captured_text")
        captured = ""
        excerpt = record.get("excerpt") if isinstance(record.get("excerpt"), str) else ""
        if not isinstance(raw_captured, str):
            audit.error("TYPE", f"{path}.captured_text", "Expected a string.")
        elif len(raw_captured) > MAX_CAPTURED_TEXT:
            audit.error("STRING_TOO_LARGE", f"{path}.captured_text", f"Captured text exceeds {MAX_CAPTURED_TEXT} characters.")
        elif not strip_pinned_whitespace(raw_captured):
            audit.error("EMPTY_STRING", f"{path}.captured_text", "Value must not be empty.")
        else:
            try:
                raw_captured.encode("utf-8")
            except UnicodeEncodeError:
                audit.error("UNICODE", f"{path}.captured_text", "String contains an invalid Unicode surrogate.")
            else:
                captured = raw_captured
            if captured and DISALLOWED_CONTROL_RE.search(captured):
                audit.error("CONTROL_CHARACTER", f"{path}.captured_text", "Captured text contains a disallowed C0 or C1 control character.")
                captured = ""
            if captured and BIDI_FORMAT_RE.search(captured):
                audit.error("BIDI_CONTROL", f"{path}.captured_text", "Captured text contains a bidirectional formatting control character.")
                captured = ""
        record["captured_text"] = captured
        if captured and excerpt and excerpt not in captured:
            audit.error("QUOTE_MISMATCH", f"{path}.excerpt", "Excerpt is not a literal substring of captured_text.")
        if excerpt and word_count(excerpt) > 25:
            audit.error("EXCERPT_TOO_LONG", f"{path}.excerpt", "Public excerpt exceeds the 25-word limit.")
        if len(excerpt) > MAX_EXCERPT_CHARS:
            audit.error("EXCERPT_TOO_LARGE", f"{path}.excerpt", f"Excerpt exceeds {MAX_EXCERPT_CHARS} characters.")
        if EMAIL_RE.search(captured) or PHONE_RE.search(captured):
            audit.warn("POSSIBLE_PII", f"{path}.captured_text", "Review and redact incidental email addresses or phone numbers.")
        record["stance"] = check_enum_string(record.get("stance"), f"{path}.stance", STANCES, audit)
        evidence_types = check_string_list(record.get("evidence_types"), f"{path}.evidence_types", audit, allow_empty=False)
        record["evidence_types"] = evidence_types
        for evidence_type in evidence_types:
            if evidence_type not in EVIDENCE_TYPES:
                audit.error("ENUM", f"{path}.evidence_types", f"Unknown evidence type {evidence_type!r}.")
        record["promotional"] = check_enum_string(
            record.get("promotional"), f"{path}.promotional", PROMOTIONAL_VALUES, audit
        )
        repost_of = record.get("repost_of")
        if repost_of is not None:
            repost_of = check_string(repost_of, f"{path}.repost_of", audit)
            record["repost_of"] = repost_of
        record["query_ids"] = check_string_list(record.get("query_ids"), f"{path}.query_ids", audit, allow_empty=False)
        record["signal_ids"] = check_string_list(record.get("signal_ids"), f"{path}.signal_ids", audit)
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
        if snapshot is None and isinstance(engagement.get("snapshot_at"), str):
            engagement["snapshot_at"] = ""
        if published and snapshot and snapshot < published:
            audit.error("TIME_ORDER", f"{path}.engagement.snapshot_at", "Engagement snapshot cannot predate publication.")
        reviews = record.get("duplicate_reviews")
        if not isinstance(reviews, list):
            audit.error("TYPE", f"{path}.duplicate_reviews", "Expected a list.")
        else:
            reviews = bounded_list(reviews, f"{path}.duplicate_reviews", audit)
            record["duplicate_reviews"] = reviews
            seen_review_ids: set[str] = set()
            for review_index, raw_review in enumerate(reviews):
                review_path = f"{path}.duplicate_reviews[{review_index}]"
                review = check_object(raw_review, review_path, {"other_source_id", "decision", "reason"}, audit)
                other_id = check_string(review.get("other_source_id"), f"{review_path}.other_source_id", audit)
                review["other_source_id"] = other_id
                if other_id == source_id:
                    audit.error("SELF_DUPLICATE_REVIEW", f"{review_path}.other_source_id", "A source cannot review itself.")
                if other_id in seen_review_ids:
                    audit.error("DUPLICATE_REVIEW", review_path, f"Source {other_id!r} is reviewed more than once.")
                seen_review_ids.add(other_id)
                review["decision"] = check_enum_string(
                    review.get("decision"), f"{review_path}.decision", {"same_source", "independent"}, audit
                )
                review["reason"] = check_string(review.get("reason"), f"{review_path}.reason", audit)
        record["notes"] = check_string(record.get("notes"), f"{path}.notes", audit, allow_empty=True)
        records.append(record)
        if source_id and source_id not in by_id:
            by_id[source_id] = record
    return records, by_id


def validate_signals(raw: Any, audit: Audit) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    catalog = check_object(raw, "signal-catalog.json", {"schema_version", "signals"}, audit)
    if not catalog:
        return [], {}
    validate_schema_version(catalog, "signal-catalog.json", audit)
    raw_signals = bounded_list(catalog.get("signals"), "signal-catalog.json.signals", audit)
    signals: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    total_citation_references = 0
    citation_budget_reported = False
    for index, raw_signal in enumerate(raw_signals):
        path = f"signal-catalog.json.signals[{index}]"
        signal = check_object(raw_signal, path, SIGNAL_REQUIRED, audit)
        if not signal:
            continue
        signal_id = check_string(signal.get("id"), f"{path}.id", audit)
        signal["id"] = signal_id
        if signal_id and not ID_RE.fullmatch(signal_id):
            audit.error("ID", f"{path}.id", "Use lowercase letters, digits, underscores, or hyphens, beginning with a letter.")
        elif signal_id and not re.fullmatch(r"sig[-_][a-z0-9][a-z0-9_-]*", signal_id):
            audit.error("ID_PREFIX", f"{path}.id", "Signal IDs must start with sig- or sig_.")
        if signal_id in by_id:
            audit.error("DUPLICATE_ID", f"{path}.id", f"Duplicate signal ID {signal_id!r}.")
        for key in ("name", "hypothesis", "decision_relevance", "disconfirming_evidence_needed"):
            signal[key] = check_string(signal.get(key), f"{path}.{key}", audit)
        for key in ("support_citations", "counter_citations", "wtp_citations"):
            signal[key] = check_string_list(signal.get(key), f"{path}.{key}", audit)
            total_citation_references += len(signal[key])
        if total_citation_references > MAX_TOTAL_CITATION_REFERENCES and not citation_budget_reported:
            audit.error(
                "REFERENCE_BUDGET",
                "signal-catalog.json.signals",
                f"Citation lists exceed the global budget of {MAX_TOTAL_CITATION_REFERENCES} references; split the study or reduce repeated citations.",
            )
            citation_budget_reported = True
        signal["alternative_explanations"] = check_string_list(signal.get("alternative_explanations"), f"{path}.alternative_explanations", audit, allow_empty=False)
        signal["claimed_level"] = check_enum_string(
            signal.get("claimed_level"), f"{path}.claimed_level", LEVELS, audit
        )
        wtp_statement = signal.get("wtp_statement")
        if wtp_statement is not None:
            signal["wtp_statement"] = check_string(wtp_statement, f"{path}.wtp_statement", audit)
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
        observations = bounded_list(observations, "research-notes.json.observations", audit)
        notes["observations"] = observations
        for index, raw_observation in enumerate(observations):
            path = f"research-notes.json.observations[{index}]"
            observation = check_object(raw_observation, path, {"text", "source_ids"}, audit)
            observation["text"] = check_string(observation.get("text"), f"{path}.text", audit)
            observation["source_ids"] = check_string_list(observation.get("source_ids"), f"{path}.source_ids", audit, allow_empty=False)
    inferences = notes.get("inferences")
    if not isinstance(inferences, list):
        audit.error("TYPE", "research-notes.json.inferences", "Expected a list.")
    else:
        inferences = bounded_list(inferences, "research-notes.json.inferences", audit)
        notes["inferences"] = inferences
        for index, raw_inference in enumerate(inferences):
            path = f"research-notes.json.inferences[{index}]"
            inference = check_object(raw_inference, path, {"text", "signal_ids"}, audit)
            inference["text"] = check_string(inference.get("text"), f"{path}.text", audit)
            inference["signal_ids"] = check_string_list(inference.get("signal_ids"), f"{path}.signal_ids", audit, allow_empty=False)
    recommendation = check_object(notes.get("recommendation"), "research-notes.json.recommendation", {"text", "signal_ids", "caveats"}, audit)
    recommendation["text"] = check_string(
        recommendation.get("text"), "research-notes.json.recommendation.text", audit
    )
    recommendation["signal_ids"] = check_string_list(recommendation.get("signal_ids"), "research-notes.json.recommendation.signal_ids", audit)
    recommendation["caveats"] = check_string_list(recommendation.get("caveats"), "research-notes.json.recommendation.caveats", audit)
    notes["next_tests"] = check_string_list(notes.get("next_tests"), "research-notes.json.next_tests", audit, allow_empty=False)
    notes["coverage_notes"] = check_string_list(notes.get("coverage_notes"), "research-notes.json.coverage_notes", audit)
    stop_reason = check_string(notes.get("stop_reason"), "research-notes.json.stop_reason", audit)
    notes["stop_reason"] = stop_reason
    recommendation_text = recommendation.get("text") if isinstance(recommendation.get("text"), str) else ""
    if normalize_text(recommendation_text) == INIT_RECOMMENDATION:
        audit.error(
            "INCOMPLETE_RESEARCH",
            "research-notes.json.recommendation.text",
            "Replace the initialization placeholder with an evidence-bound conclusion, including when no signal qualified.",
        )
    if normalize_text(stop_reason) == INIT_STOP_REASON:
        audit.error(
            "INCOMPLETE_RESEARCH",
            "research-notes.json.stop_reason",
            "Replace the initialization placeholder with the actual stopping rationale.",
        )
    return notes


def private_ngram_digest(tokens: list[str]) -> bytes:
    return hashlib.sha256("\x1f".join(tokens).encode("utf-8")).digest()[:12]


def private_identifier_components(value: str) -> list[str]:
    normalized = normalized_label(value)
    return re.findall(rf"[a-z0-9]{{{MIN_PRIVATE_IDENTIFIER_CHARS},}}", normalized)


def validate_public_output_privacy(
    plan: dict[str, Any],
    queries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    notes: dict[str, Any],
    audit: Audit,
) -> None:
    targets: list[tuple[str, str]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, str):
            targets.append((path, value))

    for field in ("question", "decision", "population", "stop_condition"):
        add(f"study-plan.json.{field}", plan.get(field))
    for field in ("inclusion_criteria", "exclusion_criteria", "limitations"):
        for index, value in enumerate(plan.get(field, [])):
            add(f"study-plan.json.{field}[{index}]", value)
    scope = plan.get("scope", {}) if isinstance(plan.get("scope"), dict) else {}
    for field in ("platforms", "communities", "languages"):
        for index, value in enumerate(scope.get(field, [])):
            add(f"study-plan.json.scope.{field}[{index}]", value)
    for index, query in enumerate(queries):
        for field in ("id", "platform", "query", "sort", "notes"):
            add(f"query-log.jsonl[{index}].{field}", query.get(field))
    for index, signal in enumerate(signals):
        for field in ("id", "name", "hypothesis", "decision_relevance", "wtp_statement", "disconfirming_evidence_needed"):
            add(f"signal-catalog.json.signals[{index}].{field}", signal.get(field))
        for field in ("alternative_explanations",):
            for value_index, value in enumerate(signal.get(field, [])):
                add(f"signal-catalog.json.signals[{index}].{field}[{value_index}]", value)
    for index, observation in enumerate(notes.get("observations", [])):
        if isinstance(observation, dict):
            add(f"research-notes.json.observations[{index}].text", observation.get("text"))
    for index, inference in enumerate(notes.get("inferences", [])):
        if isinstance(inference, dict):
            add(f"research-notes.json.inferences[{index}].text", inference.get("text"))
    recommendation = notes.get("recommendation", {}) if isinstance(notes.get("recommendation"), dict) else {}
    add("research-notes.json.recommendation.text", recommendation.get("text"))
    for index, value in enumerate(recommendation.get("caveats", [])):
        add(f"research-notes.json.recommendation.caveats[{index}]", value)
    for field in ("next_tests", "coverage_notes"):
        for index, value in enumerate(notes.get(field, [])):
            add(f"research-notes.json.{field}[{index}]", value)
    add("research-notes.json.stop_reason", notes.get("stop_reason"))
    for index, source in enumerate(sources):
        add(f"source-ledger.jsonl[{index}].id", source.get("id"))
        if source.get("visibility") == "supplied_private":
            add(f"source-ledger.jsonl[{index}].record_ref", source.get("record_ref"))
        if source.get("visibility") != "public":
            continue
        for field in ("url", "thread_url", "title", "excerpt", "notes"):
            value = source.get(field)
            if isinstance(value, str):
                targets.append((f"source-ledger.jsonl[{index}].{field}", value))

    private_ngrams: dict[int, set[bytes]] = {1: set(), 2: set(), 3: set(), 4: set()}
    private_identifier_tokens: set[bytes] = set()
    private_source_count = 0
    budget_exceeded = False
    for source in sources:
        if source.get("visibility") != "supplied_private":
            continue
        private_source_count += 1
        for field in ("captured_text", "excerpt", "title", "notes"):
            value = source.get(field)
            if not isinstance(value, str) or not strip_pinned_whitespace(value):
                continue
            normalized = normalized_label(value)
            tokens = normalized.split(" ") if normalized else []
            for component in private_identifier_components(normalized):
                private_identifier_tokens.add(private_ngram_digest([component]))
            if len(tokens) >= 4:
                candidates = (tokens[index : index + 4] for index in range(len(tokens) - 3))
                size = 4
            elif tokens and len(normalized) >= 8:
                candidates = (tokens,)
                size = len(tokens)
            else:
                candidates = ()
                size = 0
            for candidate in candidates:
                private_ngrams[size].add(private_ngram_digest(candidate))
                if (
                    sum(len(values) for values in private_ngrams.values())
                    + len(private_identifier_tokens)
                    > MAX_PRIVATE_NGRAMS
                ):
                    budget_exceeded = True
                    break
            if budget_exceeded:
                break
        if budget_exceeded:
            break
    if budget_exceeded:
        audit.error(
            "PRIVATE_SCAN_BUDGET",
            "source-ledger.jsonl",
            f"Private-text overlap scan exceeds {MAX_PRIVATE_NGRAMS} distinct bounded token sequences; split the study or reduce captured private text.",
        )
        return

    for path, value in targets:
        if not value:
            continue
        is_url_field = path.endswith(".url") or path.endswith(".thread_url")
        if EMAIL_RE.search(value) or (not is_url_field and PHONE_RE.search(value)):
            audit.error(
                "OUTPUT_PII",
                path,
                "A public artifact field contains an email address or phone-like value; redact it before building.",
            )
        if not private_source_count:
            continue
        normalized = normalized_label(value)
        tokens = normalized.split(" ") if normalized else []
        structured_identifier_target = path.endswith((".id", ".record_ref", ".url", ".thread_url"))
        ngram_overlap = structured_identifier_target and any(
            private_ngram_digest([component]) in private_identifier_tokens
            for component in private_identifier_components(normalized)
        )
        for size, digests in private_ngrams.items():
            if not digests or len(tokens) < size:
                continue
            if any(
                private_ngram_digest(tokens[index : index + size]) in digests
                for index in range(len(tokens) - size + 1)
            ):
                ngram_overlap = True
                break
        if ngram_overlap:
            audit.error(
                "PRIVATE_TEXT_IN_PUBLIC_OUTPUT",
                path,
                "A public artifact field overlaps supplied-private text; remove the passage and retain only opaque provenance.",
            )


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
        elif isinstance(target, str) and target in source_by_id:
            if source_by_id[target].get("repost_of") is not None:
                audit.error("REPOST_CHAIN", f"source-ledger.jsonl:{source_id}.repost_of", "repost_of must point directly to an origin whose repost_of is null.")
            try:
                repost_time = datetime.fromisoformat(str(source.get("published_at", "")).replace("Z", "+00:00")).astimezone(timezone.utc)
                origin_time = datetime.fromisoformat(
                    str(source_by_id[target].get("published_at", "")).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except (ValueError, OverflowError):
                pass
            else:
                if repost_time < origin_time:
                    audit.error(
                        "REPOST_TIME_ORDER",
                        f"source-ledger.jsonl:{source_id}.published_at",
                        f"A repost cannot predate its declared origin {target!r}.",
                    )


PUBLIC_IDENTITY_MATERIAL_FIELDS = (
    "platform",
    "community",
    "source_type",
    "thread_url",
    "unit_id",
    "thread_id",
    "published_at",
    "author_key",
    "language",
    "source_status",
    "title",
    "captured_text",
    "excerpt",
    "stance",
    "evidence_types",
    "promotional",
)


def public_material_value(source: dict[str, Any], field: str) -> Any:
    value = source.get(field)
    native_platform = canonical_platform(source.get("platform", "")) in {
        "reddit",
        "github",
        "hackernews",
    }
    if field == "platform":
        return canonical_platform(value)
    if field in {"community", "language"}:
        return normalized_label(value)
    if field == "thread_url" and native_platform:
        # Native slugs and path case can alias one validated thread. Its
        # URL-derived thread ID is the stable material identity.
        return normalized_label(source.get("thread_id", ""))
    if field in {"thread_url"} and isinstance(value, str):
        try:
            return canonicalize_url(value)
        except ValueError:
            return value
    if field in {"unit_id", "thread_id"} and native_platform:
        return normalized_label(value)
    if field == "published_at" and isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            return value
    if field == "evidence_types" and isinstance(value, list):
        return tuple(sorted(value))
    return value


def public_identity_conflicts(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    return [
        field
        for field in PUBLIC_IDENTITY_MATERIAL_FIELDS
        if public_material_value(left, field) != public_material_value(right, field)
    ]


def private_material_value(source: dict[str, Any], field: str) -> Any:
    """Compare repeated private provenance by field semantics, not spelling."""
    if field in {"community", "language", "published_at", "evidence_types"}:
        # These fields have the same normalization semantics in public and
        # supplied-private records. Opaque private unit/thread IDs remain exact.
        return public_material_value(source, field)
    return source.get(field)


def build_duplicate_groups(
    sources: list[dict[str, Any]], source_by_id: dict[str, dict[str, Any]], audit: Audit
) -> tuple[dict[str, str], dict[str, str]]:
    union = UnionFind(source_by_id)
    url_owner: dict[str, str] = {}
    unit_owner: dict[tuple[str, str], str] = {}
    text_owner: dict[str, str] = {}
    short_text_groups: dict[str, set[str]] = defaultdict(set)
    private_provenance_owner: dict[tuple[str, str], str] = {}
    reported_public_conflicts: set[tuple[str, str]] = set()
    reviewed_pair_decisions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for review_source in sources:
        review_source_id = review_source.get("id")
        if review_source_id not in source_by_id:
            continue
        for review in review_source.get("duplicate_reviews", []):
            if not isinstance(review, dict):
                continue
            other_id = review.get("other_source_id")
            decision = review.get("decision")
            if other_id in source_by_id and isinstance(decision, str):
                reviewed_pair_decisions[tuple(sorted((review_source_id, other_id)))].add(decision)

    def reject_public_conflict(left_id: str, right_id: str) -> None:
        pair = tuple(sorted((left_id, right_id)))
        if pair in reported_public_conflicts:
            return
        changed = public_identity_conflicts(source_by_id[left_id], source_by_id[right_id])
        if changed:
            audit.error(
                "PUBLIC_IDENTITY_CONFLICT",
                f"source-ledger.jsonl:{pair[0]}/{pair[1]}",
                f"One public unit is recorded with conflicting material fields {changed!r}; merge it into one authoritative ledger row.",
            )
            reported_public_conflicts.add(pair)
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
                if public_material_value(owner, "unit_id") != public_material_value(source, "unit_id"):
                    audit.error("DUPLICATE_METADATA_CONFLICT", f"source-ledger.jsonl:{source_id}.unit_id", f"Canonical URL is already bound to unit {owner.get('unit_id')!r}.")
                reject_public_conflict(source_id, url_owner[canonical])
                union.union(source_id, url_owner[canonical])
            else:
                url_owner[canonical] = source_id
        unit_platform = canonical_platform(source.get("platform", ""))
        submitted_unit_id = source.get("unit_id", "")
        unit_value = (
            normalized_label(submitted_unit_id)
            if unit_platform in {"reddit", "github", "hackernews"}
            else str(submitted_unit_id)
        )
        unit_key = (unit_platform, unit_value)
        if source.get("visibility") == "public" and unit_key[1]:
            if unit_key in unit_owner:
                owner = source_by_id[unit_owner[unit_key]]
                owner_url = owner.get("url") if owner.get("visibility") == "public" else None
                if canonical and isinstance(owner_url, str):
                    try:
                        owner_canonical = canonicalize_url(owner_url)
                    except ValueError:
                        owner_canonical = ""
                    if (
                        owner_canonical
                        and owner_canonical != canonical
                        and unit_platform not in {"reddit", "github", "hackernews"}
                    ):
                        audit.error("DUPLICATE_METADATA_CONFLICT", f"source-ledger.jsonl:{source_id}.url", f"Platform unit ID is already bound to {owner_canonical!r}.")
                reject_public_conflict(source_id, unit_owner[unit_key])
                union.union(source_id, unit_owner[unit_key])
            else:
                unit_owner[unit_key] = source_id
        if source.get("visibility") == "supplied_private":
            provenance_key = (
                str(source.get("source_file_sha256", "")),
                str(source.get("record_ref", "")),
            )
            if all(provenance_key):
                if provenance_key in private_provenance_owner:
                    owner_id = private_provenance_owner[provenance_key]
                    owner = source_by_id[owner_id]
                    material_fields = (
                        "community",
                        "unit_id",
                        "thread_id",
                        "published_at",
                        "author_key",
                        "language",
                        "source_status",
                        "title",
                        "captured_text",
                        "excerpt",
                        "stance",
                        "evidence_types",
                        "promotional",
                    )
                    changed = [
                        field
                        for field in material_fields
                        if private_material_value(owner, field) != private_material_value(source, field)
                    ]
                    if changed:
                        audit.error(
                            "PRIVATE_PROVENANCE_CONFLICT",
                            f"source-ledger.jsonl:{source_id}",
                            f"Private provenance is already used by {owner_id!r} with conflicting fields {changed!r}; use one record per exported unit.",
                        )
                    union.union(source_id, owner_id)
                else:
                    private_provenance_owner[provenance_key] = source_id
        normalized = ascii_casefold(html.unescape(normalize_text(str(source.get("captured_text", "")))))
        if len(normalized) >= MIN_EXACT_DUPLICATE_CHARS and word_count(normalized) >= MIN_EXACT_DUPLICATE_WORDS:
            content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if content_hash in text_owner:
                union.union(source_id, text_owner[content_hash])
            else:
                text_owner[content_hash] = source_id
        elif len(normalized) >= MIN_SHORT_EXACT_CHARS and word_count(normalized) >= MIN_SHORT_EXACT_WORDS:
            content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            short_text_groups[content_hash].add(source_id)
    duplicate_root = {source_id: union.find(source_id) for source_id in source_by_id}

    # Short exact text is review-only, so wait until every hard duplicate edge
    # has been applied before deciding which distinct groups still need review.
    # Map explicit independence decisions through the completed union graph: a
    # review between any two members is a decision about their final groups.
    independent_root_pairs: set[tuple[str, str]] = set()
    for (left_id, right_id), decisions in reviewed_pair_decisions.items():
        if "independent" not in decisions:
            continue
        left_root = duplicate_root[left_id]
        right_root = duplicate_root[right_id]
        if left_root != right_root:
            independent_root_pairs.add(tuple(sorted((left_root, right_root))))
    independent_neighbors: dict[str, set[str]] = defaultdict(set)
    for left_root, right_root in independent_root_pairs:
        independent_neighbors[left_root].add(right_root)
        independent_neighbors[right_root].add(left_root)

    for content_hash in sorted(short_text_groups):
        representative_by_root: dict[str, str] = {}
        for source_id in short_text_groups[content_hash]:
            root = duplicate_root[source_id]
            current = representative_by_root.get(root)
            if current is None or source_id < current:
                representative_by_root[root] = source_id
        if len(representative_by_root) < 2:
            continue
        ordered = sorted(
            representative_by_root.items(),
            key=lambda item: (item[1], item[0]),
        )
        group_roots = set(representative_by_root)
        root_index = {root: index for index, (root, _) in enumerate(ordered)}
        reviewed_later_counts: Counter[str] = Counter()
        for left_root in group_roots:
            for right_root in independent_neighbors.get(left_root, set()):
                if right_root in group_roots and root_index[left_root] < root_index[right_root]:
                    reviewed_later_counts[left_root] += 1
        reviewed_edge_count = sum(reviewed_later_counts.values())
        required_edge_count = len(group_roots) * (len(group_roots) - 1) // 2
        if reviewed_edge_count == required_edge_count:
            continue

        # Independence is pair-specific, not transitive. Find the first
        # unresolved final-root pair deterministically without enumerating a
        # complete O(k^2) pair matrix when all (or nearly all) pairs are done.
        missing_pair: tuple[str, str] | None = None
        for left_index, (left_root, left_id) in enumerate(ordered[:-1]):
            neighbors = independent_neighbors.get(left_root, set())
            if reviewed_later_counts[left_root] == len(ordered) - left_index - 1:
                continue
            for right_root, right_id in ordered[left_index + 1 :]:
                if right_root not in neighbors:
                    missing_pair = (left_id, right_id)
                    break
            if missing_pair is not None:
                break
        if missing_pair is not None:
            audit.warn(
                "POSSIBLE_SHORT_EXACT_DUPLICATE",
                f"source-ledger.jsonl:{missing_pair[0]}/{missing_pair[1]}",
                "Short captured_text is exactly equal for an unresolved pair; record a pair-specific duplicate_review because independence is not transitive.",
            )

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
        except (ValueError, OverflowError):
            published = datetime.max.replace(tzinfo=timezone.utc)
        return explicit_priority, published, source_id

    origin_by_root = {root: min(group_members, key=published_key) for root, group_members in members.items()}
    return duplicate_root, origin_by_root


def describe_duplicate_groups(
    sources: list[dict[str, Any]],
    duplicate_root: dict[str, str],
    origin_by_root: dict[str, str],
) -> list[dict[str, Any]]:
    source_by_id = {str(source.get("id")): source for source in sources if source.get("id")}
    members_by_root: dict[str, list[str]] = defaultdict(list)
    for source_id, root in duplicate_root.items():
        members_by_root[root].append(source_id)
    details: list[dict[str, Any]] = []
    for root, unsorted_members in members_by_root.items():
        members = sorted(unsorted_members)
        if len(members) < 2:
            continue
        member_set = set(members)
        reasons: set[str] = set()
        canonical_counts: Counter[str] = Counter()
        unit_counts: Counter[tuple[str, str]] = Counter()
        text_counts: Counter[str] = Counter()
        private_provenance_counts: Counter[tuple[str, str]] = Counter()
        for source_id in members:
            source = source_by_id[source_id]
            if source.get("repost_of") in member_set:
                reasons.add("explicit_repost")
            for review in source.get("duplicate_reviews", []):
                if (
                    isinstance(review, dict)
                    and review.get("decision") == "same_source"
                    and review.get("other_source_id") in member_set
                ):
                    reasons.add("reviewed_same_source")
            if source.get("visibility") == "public" and isinstance(source.get("url"), str):
                try:
                    canonical_counts[canonicalize_url(source["url"])] += 1
                except ValueError:
                    pass
            unit_platform = canonical_platform(source.get("platform", ""))
            submitted_unit_id = source.get("unit_id", "")
            unit_key = (
                unit_platform,
                normalized_label(submitted_unit_id)
                if unit_platform in {"reddit", "github", "hackernews"}
                else str(submitted_unit_id),
            )
            if source.get("visibility") == "public" and unit_key[1]:
                unit_counts[unit_key] += 1
            if source.get("visibility") == "supplied_private":
                provenance_key = (
                    str(source.get("source_file_sha256", "")),
                    str(source.get("record_ref", "")),
                )
                if all(provenance_key):
                    private_provenance_counts[provenance_key] += 1
            normalized = ascii_casefold(html.unescape(normalize_text(str(source.get("captured_text", "")))))
            if len(normalized) >= MIN_EXACT_DUPLICATE_CHARS and word_count(normalized) >= MIN_EXACT_DUPLICATE_WORDS:
                text_counts[hashlib.sha256(normalized.encode("utf-8")).hexdigest()] += 1
        if any(count > 1 for count in canonical_counts.values()):
            reasons.add("canonical_url")
        if any(count > 1 for count in unit_counts.values()):
            reasons.add("platform_unit_id")
        if any(count > 1 for count in private_provenance_counts.values()):
            reasons.add("private_provenance")
        if any(count > 1 for count in text_counts.values()):
            reasons.add("exact_captured_text")
        details.append(
            {
                "group_root_source_id": root,
                "origin_source_id": origin_by_root.get(root, root),
                "member_source_ids": members,
                "collapse_reasons": sorted(reasons) or ["transitive_same_source_link"],
            }
        )
    return sorted(
        details,
        key=lambda item: (str(item["origin_source_id"]), item["member_source_ids"]),
    )


def detect_fuzzy_duplicates(
    sources: list[dict[str, Any]], duplicate_root: dict[str, str], audit: Audit
) -> None:
    eligible: list[tuple[str, int, set[int]]] = []
    reviewed: dict[tuple[str, str], str] = {}
    stored_shingles = 0
    for source in sources:
        source_id = source.get("id")
        if not isinstance(source_id, str):
            continue
        for review in source.get("duplicate_reviews", []):
            if isinstance(review, dict) and isinstance(review.get("other_source_id"), str):
                reviewed[tuple(sorted((source_id, review["other_source_id"])))] = str(review.get("decision"))
        normalized = ascii_casefold(html.unescape(normalize_text(str(source.get("captured_text", "")))))
        tokens = normalized.split(" ") if normalized else []
        if len(tokens) < 30:
            continue
        shingles: set[int] = set()
        for index in range(len(tokens) - 4):
            digest = hashlib.blake2b(
                "\x1f".join(tokens[index : index + 5]).encode("utf-8"),
                digest_size=8,
            ).digest()
            shingles.add(int.from_bytes(digest, "big"))
            if stored_shingles + len(shingles) > MAX_FUZZY_STORED_SHINGLES:
                audit.warn(
                    "FUZZY_SCAN_SKIPPED",
                    "source-ledger.jsonl",
                    f"Fuzzy duplicate preparation exceeds {MAX_FUZZY_STORED_SHINGLES} stored shingle digests; block or review the long records externally.",
                )
                return
        stored_shingles += len(shingles)
        eligible.append((source_id, len(tokens), shingles))
    pair_count = len(eligible) * (len(eligible) - 1) // 2
    shingle_work = max(0, len(eligible) - 1) * sum(len(shingles) for _, _, shingles in eligible)
    if pair_count > MAX_FUZZY_PAIRS or shingle_work > MAX_FUZZY_SHINGLE_WORK:
        audit.warn(
            "FUZZY_SCAN_SKIPPED",
            "source-ledger.jsonl",
            f"Fuzzy duplicate scan would require {pair_count} pairs and {shingle_work} shingle lookups, above budgets of {MAX_FUZZY_PAIRS} and {MAX_FUZZY_SHINGLE_WORK}; block or review the long records externally.",
        )
        return
    for left_index, (left_id, left_token_count, left_shingles) in enumerate(eligible):
        for right_id, right_token_count, right_shingles in eligible[left_index + 1 :]:
            if duplicate_root.get(left_id) == duplicate_root.get(right_id):
                continue
            length_ratio = min(left_token_count, right_token_count) / max(left_token_count, right_token_count)
            if length_ratio < 0.90:
                continue
            smaller, larger = (
                (left_shingles, right_shingles)
                if len(left_shingles) <= len(right_shingles)
                else (right_shingles, left_shingles)
            )
            intersection_size = sum(shingle in larger for shingle in smaller)
            union_size = len(left_shingles) + len(right_shingles) - intersection_size
            similarity = intersection_size / union_size if union_size else 0.0
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
    empty_ids: set[str] = set()
    query_included_ids = {
        query_id: set(query.get("included_source_ids", []))
        for query_id, query in query_by_id.items()
    }
    source_query_ids = {
        source_id: set(source.get("query_ids", []))
        for source_id, source in source_by_id.items()
    }
    source_signal_ids = {
        source_id: set(source.get("signal_ids", []))
        for source_id, source in source_by_id.items()
    }
    signal_support_ids = {
        signal_id: set(signal.get("support_citations", []))
        for signal_id, signal in signal_by_id.items()
    }
    signal_cited_ids = {
        signal_id: signal_support_ids[signal_id] | set(signal.get("counter_citations", []))
        for signal_id, signal in signal_by_id.items()
    }
    for query in queries:
        query_id = query.get("id", "")
        for source_id in query.get("included_source_ids", []):
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"query-log.jsonl:{query_id}.included_source_ids", f"Unknown source ID {source_id!r}.")
            elif query_id not in source_query_ids.get(source_id, empty_ids):
                audit.error("QUERY_LINK_MISMATCH", f"query-log.jsonl:{query_id}", f"Source {source_id!r} does not link back to query {query_id!r}.")
        for signal_id in query.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", f"query-log.jsonl:{query_id}.signal_ids", f"Unknown signal ID {signal_id!r}.")
    for source in sources:
        source_id = source.get("id", "")
        for query_id in source.get("query_ids", []):
            if query_id not in query_by_id:
                audit.error("BAD_QUERY_REF", f"source-ledger.jsonl:{source_id}.query_ids", f"Unknown query ID {query_id!r}.")
                continue
            query = query_by_id[query_id]
            if source_id not in query_included_ids.get(query_id, empty_ids):
                audit.error("QUERY_LINK_MISMATCH", f"source-ledger.jsonl:{source_id}", f"Query {query_id!r} does not include source {source_id!r}.")
            if canonical_platform(query.get("platform", "")) != canonical_platform(source.get("platform", "")):
                audit.error(
                    "QUERY_PLATFORM_MISMATCH",
                    f"source-ledger.jsonl:{source_id}.query_ids",
                    f"Query {query_id!r} and its included source must use the same normalized platform.",
                )
            query_run = parse_datetime(query.get("run_at"), f"query-log.jsonl:{query_id}.run_at", audit)
            source_published = parse_datetime(source.get("published_at"), f"source-ledger.jsonl:{source_id}.published_at", audit)
            if query_run and source_published and source_published > query_run:
                audit.error(
                    "QUERY_SOURCE_TIME_ORDER",
                    f"source-ledger.jsonl:{source_id}.published_at",
                    f"Source publication is later than linked query {query_id!r}; it could not have been returned by that execution.",
                )
        for signal_id in source.get("signal_ids", []):
            if signal_id not in signal_by_id:
                audit.error("BAD_SIGNAL_REF", f"source-ledger.jsonl:{source_id}.signal_ids", f"Unknown signal ID {signal_id!r}.")
            else:
                if source_id not in signal_cited_ids.get(signal_id, empty_ids):
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
                if signal_id not in source_signal_ids.get(source_id, empty_ids):
                    audit.error("SIGNAL_LINK_MISMATCH", f"signal-catalog.json:{signal_id}.{field}", f"Source {source_id!r} does not link back to signal {signal_id!r}.")
        for source_id in signal.get("wtp_citations", []):
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"signal-catalog.json:{signal_id}.wtp_citations", f"Unknown source ID {source_id!r}.")
                continue
            source = source_by_id[source_id]
            if source_id not in signal_support_ids.get(signal_id, empty_ids):
                audit.error("WTP_NOT_SUPPORT", f"signal-catalog.json:{signal_id}.wtp_citations", f"WTP source {source_id!r} must also be a support citation.")
            if not is_eligible_positive_source(source) or not ({"purchase_intent", "observed_payment"} & set(source.get("evidence_types", []))):
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
        observation_ids = observation.get("source_ids", [])
        if len(observation_ids) != 1:
            audit.error(
                "OBSERVATION_SOURCE_COUNT",
                f"research-notes.json.observations[{index}].source_ids",
                "A cited observation must bind one literal public source excerpt; put cross-source synthesis in inferences.",
            )
        for source_id in observation_ids:
            if source_id not in source_by_id:
                audit.error("BAD_SOURCE_REF", f"research-notes.json.observations[{index}].source_ids", f"Unknown source ID {source_id!r}.")
                continue
            source = source_by_id[source_id]
            if source.get("visibility") != "public":
                audit.error(
                    "PRIVATE_OBSERVATION",
                    f"research-notes.json.observations[{index}]",
                    "Supplied-private text cannot be rendered as an observation; cite its withheld provenance in the signal evidence instead.",
                )
            elif observation.get("text") != source.get("excerpt"):
                audit.error(
                    "OBSERVATION_NOT_LITERAL",
                    f"research-notes.json.observations[{index}].text",
                    f"Observation text must exactly equal the literal excerpt of {source_id!r}.",
                )
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


def index_counter_queries_by_signal(
    queries: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Index counter-query links once instead of rescanning per signal."""
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query in queries:
        if query.get("intent") != "counter":
            continue
        for signal_id in query.get("signal_ids", []):
            if isinstance(signal_id, str):
                indexed[signal_id].append(query)
    return dict(indexed)


def calculate_signal_metrics(
    signal: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    duplicate_root: dict[str, str],
    origin_by_root: dict[str, str],
    plan: dict[str, Any],
    linked_counter_queries: list[dict[str, Any]],
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
    known_support = [
        source
        for source in all_support
        if source.get("stance") == "support" and source.get("author_key") != "unknown"
    ]
    eligible = [source for source in all_support if is_eligible_positive_source(source)]
    ineligible = [
        source
        for source in all_support
        if source.get("stance") == "support" and not is_eligible_positive_source(source)
    ]
    unclear = [source for source in known_support if source.get("promotional") == "unclear"]
    counters = representative_records(counter_ids, source_by_id, duplicate_root, origin_by_root)
    authors = {source.get("author_key") for source in eligible if source.get("author_key") != "unknown"}
    threads = {thread_identity_key(source) for source in eligible}
    communities = {normalized_label(source.get("community")) for source in eligible if source.get("community")}
    platforms = {source_platform_identity(source) for source in eligible}
    ranked_types = sorted({kind for source in eligible for kind in source.get("evidence_types", []) if kind in RANKED_EVIDENCE_TYPES})
    costly_types = sorted({kind for source in eligible for kind in source.get("evidence_types", []) if kind in COSTLY_BEHAVIOR_TYPES})
    risky_promotion = [source for source in known_support if source.get("promotional") in {"yes", "unclear"}]
    promotion_risk_share = len(risky_promotion) / len(known_support) if known_support else 0.0
    unclear_share = len(unclear) / (len(eligible) + len(unclear)) if eligible or unclear else 0.0
    complete_counter_queries = [
        query for query in linked_counter_queries if qualifying_query_execution(query, require_complete=True)
    ]
    counter_query_count = len(linked_counter_queries)
    if plan.get("counterevidence_status") == "complete" and complete_counter_queries:
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
        except (ValueError, OverflowError):
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

    declared_wtp_records = representative_records(
        set(signal.get("wtp_citations", [])), source_by_id, duplicate_root, origin_by_root
    )
    wtp_records = [
        source
        for source in declared_wtp_records
        if is_eligible_positive_source(source)
        and {"purchase_intent", "observed_payment"} & set(source.get("evidence_types", []))
    ]
    wtp_authors = {source.get("author_key") for source in wtp_records if source.get("author_key") != "unknown"}
    wtp_threads = {thread_identity_key(source) for source in wtp_records}
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
        "complete_counter_query_count": len(complete_counter_queries),
        "wtp_status": wtp_status,
        "wtp_evidence": wtp_evidence,
        "wtp_authors": len(wtp_authors),
        "wtp_threads": len(wtp_threads),
        "wtp_source_ids": [source.get("id") for source in wtp_records],
        "support_source_ids": [source.get("id") for source in eligible],
        "ineligible_support_source_ids": [source.get("id") for source in ineligible],
        "counter_source_ids": [source.get("id") for source in counters],
    }


def ratio_points(actual: int, target: int) -> float:
    if target <= 0:
        return 0.0
    return 10.0 * min(1.0, actual / target)


def qualifying_query_execution(query: dict[str, Any], *, require_complete: bool = False) -> bool:
    seen = query.get("results_seen")
    screened = query.get("results_screened")
    pages = query.get("pages_seen")
    valid_counts = (
        isinstance(seen, int)
        and not isinstance(seen, bool)
        and seen >= 0
        and isinstance(screened, int)
        and not isinstance(screened, bool)
        and 0 <= screened <= seen
        and isinstance(pages, int)
        and not isinstance(pages, bool)
        and pages >= 1
        and (seen == 0 or screened > 0)
    )
    return valid_counts and (not require_complete or query.get("truncated") is False)


def calculate_execution_coverage(
    plan: dict[str, Any],
    queries: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    signals: list[dict[str, Any]],
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
        "threads": len({thread_identity_key(source) for source in unique_sources}),
        "communities": len({normalized_label(source.get("community")) for source in unique_sources if source.get("community")}),
        "platforms": len({source_platform_identity(source) for source in unique_sources}),
        "counter_queries": sum(
            query.get("intent") == "counter" and qualifying_query_execution(query)
            for query in queries
        ),
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

    link_issue_codes = {
        "BAD_SOURCE_REF",
        "BAD_QUERY_REF",
        "QUERY_LINK_MISMATCH",
        "QUERY_PLATFORM_MISMATCH",
        "QUERY_SOURCE_TIME_ORDER",
    }
    if queries and sources and not any(issue.code in link_issue_codes for issue in audit.issues):
        score += 10.0
    else:
        audit.warn("VACUOUS_LINK_COVERAGE", "query-log.jsonl", "Query/source reconciliation earns coverage only when both records exist.")
    if any(
        query.get("intent") in {"neutral", "counter"} and qualifying_query_execution(query)
        for query in queries
    ):
        score += 10.0
    else:
        audit.warn("NO_BALANCING_QUERY", "query-log.jsonl", "Log at least one neutral or counter-oriented query.")
    if plan.get("counterevidence_status") == "complete" and any(
        query.get("intent") == "counter" and qualifying_query_execution(query, require_complete=True)
        for query in queries
    ):
        score += 10.0
    else:
        audit.warn(
            "COUNTEREVIDENCE_INCOMPLETE",
            "study-plan.json.counterevidence_status",
            "Counterevidence must be marked complete and backed by a non-truncated counter query with a viewed result page and screening when results were returned.",
        )

    eligible_support = [source for source in unique_sources if is_eligible_positive_source(source)]
    if eligible_support:
        thread_counts = Counter(thread_identity_key(source) for source in eligible_support)
        max_share = max(thread_counts.values()) / len(eligible_support)
        if max_share <= 0.5:
            score += 10.0
        else:
            audit.warn("THREAD_CONCENTRATION", "source-ledger.jsonl", f"One thread supplies {max_share:.0%} of eligible supporting source units.")
    else:
        max_share = 0.0
        if any(signal.get("claimed_level") != "unsupported" for signal in signals):
            audit.warn("NO_ELIGIBLE_SUPPORT", "source-ledger.jsonl", "No eligible supporting evidence was captured for a signal claiming positive support.")

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
        # Unknown fields are structurally invalid and have no semantic meaning;
        # excluding them also prevents misspelled private prose fields from
        # becoming a low-entropy public fingerprint oracle.
        prepared = {key: source[key] for key in SOURCE_REQUIRED if key in source}
        if source.get("visibility") != "public":
            # Do not publish an offline dictionary oracle for short private
            # responses. This conservative branch also protects structurally
            # invalid rows whose intended private visibility is misspelled or
            # missing. Valid public evidence remains fully bound.
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
                "source_groups_after_collapse": 0,
                "collapsed_duplicate_groups": 0,
                "signals": len(signals),
                "errors": len(audit.errors),
                "warnings": len(audit.warnings),
            },
            "coverage_execution_score": 0.0,
            "coverage": {},
            "duplicate_groups": [],
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
    duplicate_groups = describe_duplicate_groups(sources, duplicate_root, origin_by_root)
    detect_fuzzy_duplicates(sources, duplicate_root, audit)
    reconcile_links(queries, query_by_id, sources, source_by_id, signals, signal_by_id, notes, audit)
    validate_public_output_privacy(plan, queries, sources, signals, notes, audit)

    counter_queries_by_signal = index_counter_queries_by_signal(queries)
    metrics = [
        calculate_signal_metrics(
            signal,
            source_by_id,
            duplicate_root,
            origin_by_root,
            plan,
            counter_queries_by_signal.get(str(signal.get("id", "")), []),
            audit,
        )
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
    execution_score, coverage = calculate_execution_coverage(
        plan,
        queries,
        sources,
        signals,
        duplicate_root,
        origin_by_root,
        audit,
    ) if plan else (0.0, {})

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
            "source_groups_after_collapse": len(set(duplicate_root.values())),
            "collapsed_duplicate_groups": len(duplicate_groups),
            "signals": len(signals),
            "errors": len(audit.errors),
            "warnings": len(audit.warnings),
        },
        "coverage_execution_score": execution_score,
        "coverage": coverage,
        "duplicate_groups": duplicate_groups,
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
    text = BIDI_FORMAT_RE.sub(lambda match: f"\\u{ord(match.group(0)):04x}", text)
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)


def markdown_url(value: str) -> str:
    return value.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def safe_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    first_visible_index = 0
    while first_visible_index < len(value):
        codepoint = ord(value[first_visible_index])
        if codepoint > 0x1F and codepoint not in PINNED_WHITESPACE_CODEPOINTS:
            break
        first_visible_index += 1
    first_visible = value[first_visible_index:]
    if first_visible.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class BoundedUtf8Buffer:
    """Accumulate one generated artifact while enforcing its byte cap eagerly."""

    def __init__(self, artifact_name: str) -> None:
        self.artifact_name = artifact_name
        self.byte_count = 0
        self._chunks: list[str] = []

    def write(self, value: str) -> int:
        size = len(value.encode("utf-8"))
        if self.byte_count + size > MAX_GENERATED_ARTIFACT_BYTES:
            raise ValueError(
                f"Generated {self.artifact_name} exceeds the "
                f"{MAX_GENERATED_ARTIFACT_BYTES}-byte artifact safety limit"
            )
        self.byte_count += size
        self._chunks.append(value)
        return len(value)

    def getvalue(self) -> str:
        return "".join(self._chunks)


class BoundedLineAccumulator:
    """Append newline-delimited text without ever crossing the artifact cap."""

    def __init__(self, artifact_name: str) -> None:
        self._buffer = BoundedUtf8Buffer(artifact_name)
        self._has_line = False

    def append(self, value: Any) -> None:
        if self._has_line:
            self._buffer.write("\n")
        self._buffer.write(str(value))
        self._has_line = True

    def extend(self, values: Iterable[Any]) -> None:
        for value in values:
            self.append(value)

    def finish(self, suffix: str = "") -> str:
        if suffix:
            self._buffer.write(suffix)
        return self._buffer.getvalue()


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
    buffer = BoundedUtf8Buffer("signals.csv")
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
        lines = BoundedLineAccumulator("findings.md")
        lines.extend([
            "# Community signal build failed",
            "",
            f"Input fingerprint: `{fingerprint}`",
            "",
            "No ranked findings were generated because integrity checks failed.",
            "",
            "## Errors",
            "",
        ])
        for issue in report.get("issues", []):
            if issue.get("severity") == "error":
                lines.append(f"- `{markdown_escape(issue.get('code'))}` at `{markdown_escape(issue.get('path'))}`: {markdown_escape(issue.get('message'))}")
        return lines.finish("\n")

    plan = context.get("plan", {})
    queries = sorted(context.get("queries", []), key=lambda query: str(query.get("id", "")))
    notes = context.get("notes", {})
    source_by_id = context.get("source_by_id", {})
    signal_by_id = context.get("signal_by_id", {})
    lines = BoundedLineAccumulator("findings.md")
    lines.extend([
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
        "> Supplied-private source-file hashes are caller-declared provenance. The helper preserves their format and value but does not receive the underlying files and cannot authenticate the file/digest relationship.",
        "",
        "## Ranked hypotheses",
        "",
        "| Rank | Signal | Declared label | Computed ceiling | Evidence score | Author keys | Threads | Excluded cited support | Counter sources | WTP |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for metric in report.get("signals", []):
        lines.append(
            f"| {metric['rank']} | {markdown_escape(metric['name'])} | `{metric['claimed_level']}` | `{metric['calculated_level']}` | {metric['evidence_score']} | {metric['distinct_author_keys']} | {metric['distinct_threads']} | {metric['ineligible_support_groups']} | {metric['counter_sources']} | `{metric['wtp_status']}` |"
        )
    if not report.get("signals"):
        lines.append("| — | No hypotheses supplied | `unsupported` | `unsupported` | 0 | 0 | 0 | 0 | 0 | `none` |")

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
                f"**Declared evidence label:** `{metric['claimed_level']}`. **Computed ceiling:** `{metric['calculated_level']}` from {metric['distinct_author_keys']} distinct observed author keys across {metric['distinct_threads']} threads and {metric['communities']} communities. Score {metric['evidence_score']}/100 within this sample.",
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
                    "These promotional, promotion-unclear, or unknown-author records remain visible but do not affect labels, ranks, scores, or willingness-to-pay counts.",
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
        lines.extend(
            [
                "### Willingness to pay",
                "",
                f"**Computed WTP status:** `{metric['wtp_status']}`; basis `{metric['wtp_evidence']}`; {metric['wtp_authors']} observed author keys across {metric['wtp_threads']} threads.",
                "",
            ]
        )
        if metric.get("wtp_source_ids"):
            for source_id in metric["wtp_source_ids"]:
                lines.append(render_source(source_by_id[source_id]))
                lines.append("")
        else:
            lines.extend(["No eligible cited purchase-intent or observed-payment source is present.", ""])
        alternatives = signal.get("alternative_explanations", [])
        lines.extend(["### What could falsify or reframe this", ""])
        for alternative in alternatives:
            lines.append(f"- {markdown_escape(alternative)}")
        lines.append(f"- Evidence needed: {markdown_escape(signal.get('disconfirming_evidence_needed', ''))}")

    lines.extend(["", "## Duplicate and repost groups", ""])
    duplicate_groups = report.get("duplicate_groups", [])
    if duplicate_groups:
        lines.extend(
            [
                "| Origin | Collapsed members | Reasons |",
                "| --- | --- | --- |",
            ]
        )
        for group in duplicate_groups:
            members = ", ".join(f"`{markdown_escape(source_id)}`" for source_id in group.get("member_source_ids", []))
            reasons = ", ".join(f"`{markdown_escape(reason)}`" for reason in group.get("collapse_reasons", []))
            lines.append(
                f"| `{markdown_escape(group.get('origin_source_id', ''))}` | {members} | {reasons} |"
            )
    else:
        lines.append("No multi-source duplicate or repost groups were collapsed.")

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
            lines.append(f"- Cited source excerpt ({citations}): {markdown_escape(observation.get('text', ''))}")
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
    return lines.finish()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(path.parent):
        raise OSError(f"Refusing to write through linked or reparse-point directory: {path.parent}")
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
    if is_link_or_reparse(path):
        raise ValueError(f"Refusing linked or reparse-point study directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if is_link_or_reparse(path):
        raise ValueError(f"Refusing linked or reparse-point study directory: {path}")
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
    audit_buffer = BoundedUtf8Buffer("audit.json")
    encoder = json.JSONEncoder(indent=2, ensure_ascii=False, sort_keys=True)
    for chunk in encoder.iterencode(committed_report):
        audit_buffer.write(chunk)
    audit_buffer.write("\n")
    audit_content = audit_buffer.getvalue()
    contents = {"signals.csv": csv_content, "findings.md": findings_content, "audit.json": audit_content}
    return contents


def safe_artifacts_dir(study_dir: Path, *, create: bool) -> Path:
    artifacts_dir = study_dir / "artifacts"
    if is_link_or_reparse(artifacts_dir):
        raise ValueError(f"Refusing linked or reparse-point artifact directory: {artifacts_dir}")
    if artifacts_dir.exists():
        if not artifacts_dir.resolve().is_relative_to(study_dir.resolve()):
            raise ValueError(f"Refusing artifact directory outside the study root: {artifacts_dir}")
        if not artifacts_dir.is_dir():
            raise ValueError(f"Artifact path is not a directory: {artifacts_dir}")
    elif create:
        artifacts_dir.mkdir(parents=False)
    for name in ARTIFACT_NAMES:
        target = artifacts_dir / name
        if is_link_or_reparse(target):
            raise ValueError(f"Refusing linked or reparse-point artifact target: {target}")
        if target.exists() and (
            not target.is_file()
            or not target.resolve().is_relative_to(artifacts_dir.resolve())
        ):
            raise ValueError(f"Artifact target must be a regular file inside the generated directory: {target}")
    return artifacts_dir


def bounded_directory_entries(directory: Path, limit: int) -> tuple[list[Path], bool]:
    """Read at most limit entries plus one overflow sentinel from a directory."""
    if is_link_or_reparse(directory):
        raise ValueError(f"Refusing to scan linked or reparse-point directory: {directory}")
    entries: list[Path] = []
    with os.scandir(directory) as iterator:
        for index, entry in enumerate(iterator):
            if index >= limit:
                return entries, True
            entries.append(Path(entry.path))
    return entries, False


def _transaction_token(path: Path, prefix: str) -> str | None:
    if not path.name.startswith(prefix):
        return None
    token = path.name.removeprefix(prefix)
    return token if TRANSACTION_TOKEN_RE.fullmatch(token) else None


def _validate_exact_artifact_directory(directory: Path, study_dir: Path, message: str) -> None:
    if (
        is_link_or_reparse(directory)
        or not directory.is_dir()
        or not directory.resolve().is_relative_to(study_dir.resolve())
    ):
        raise ValueError(message)
    entries, overflow = bounded_directory_entries(directory, len(ARTIFACT_NAMES))
    directory_root = directory.resolve()
    if overflow or {entry.name for entry in entries} != set(ARTIFACT_NAMES) or any(
        is_link_or_reparse(entry)
        or not entry.is_file()
        or not entry.resolve().is_relative_to(directory_root)
        for entry in entries
    ):
        raise ValueError(message)


def _validate_disposable_stage(directory: Path, study_dir: Path) -> None:
    """Validate the bounded partial state left while staging generated files."""
    message = "Interrupted artifact stage contains unsafe or unexpected data; inspect it before retrying the build"
    if (
        is_link_or_reparse(directory)
        or not directory.is_dir()
        or not directory.resolve().is_relative_to(study_dir.resolve())
    ):
        raise ValueError(message)
    # At a hard stop, atomic_write_text can leave the completed predecessors,
    # plus at most one temporary file for the artifact currently being written.
    entries, overflow = bounded_directory_entries(directory, len(ARTIFACT_NAMES) + 1)
    directory_root = directory.resolve()
    temporary_count = 0
    for entry in entries:
        is_temporary = entry.name.startswith(".csr-") and entry.name.endswith(".tmp")
        temporary_count += int(is_temporary)
        if (
            (entry.name not in ARTIFACT_NAMES and not is_temporary)
            or is_link_or_reparse(entry)
            or not entry.is_file()
            or not entry.resolve().is_relative_to(directory_root)
        ):
            raise ValueError(message)
    if overflow or temporary_count > 1:
        raise ValueError(message)


def _remove_validated_transaction_directory(directory: Path) -> None:
    # Recheck the directory itself immediately before deletion so a linked
    # replacement is never traversed after the earlier content validation.
    if is_link_or_reparse(directory) or not directory.is_dir():
        raise ValueError("Refusing to remove a linked or replaced transaction directory")
    shutil.rmtree(directory)


def _recover_interrupted_artifact_swap_unlocked(study_dir: Path) -> bool:
    """Recover or clean a bounded, recognizable interrupted artifact transaction."""
    if is_link_or_reparse(study_dir):
        raise ValueError("Refusing recovery through a linked or reparse-point study directory")
    artifacts_dir = study_dir / "artifacts"
    study_entries, study_overflow = bounded_directory_entries(study_dir, MAX_STUDY_DIRECTORY_ENTRIES)
    if study_overflow:
        raise ValueError(f"Study directory exceeds the {MAX_STUDY_DIRECTORY_ENTRIES}-entry recovery scan limit")
    backup_prefix = ".csr-artifacts-backup-"
    stage_prefix = ".csr-artifacts-stage-"
    generated_backups = {
        token: path
        for path in study_entries
        if (token := _transaction_token(path, backup_prefix)) is not None
    }
    generated_stages = {
        token: path
        for path in study_entries
        if (token := _transaction_token(path, stage_prefix)) is not None
    }

    if is_link_or_reparse(artifacts_dir):
        raise ValueError("Refusing linked or reparse-point live artifact directory")
    if artifacts_dir.exists():
        if not generated_backups and not generated_stages:
            return False
        _validate_exact_artifact_directory(
            artifacts_dir,
            study_dir,
            "Live artifact directory is not an exact safe generated-artifact set; refusing transaction cleanup",
        )
        overlapping_tokens = sorted(generated_backups.keys() & generated_stages.keys())
        if overlapping_tokens:
            raise ValueError("Live artifacts coexist with matching stage and backup directories; inspect the ambiguous transaction")
        for backup in generated_backups.values():
            _validate_exact_artifact_directory(
                backup,
                study_dir,
                "Interrupted artifact backup must contain exactly the three regular generated artifacts",
            )
        for stage in generated_stages.values():
            _validate_disposable_stage(stage, study_dir)
        for stage in generated_stages.values():
            _remove_validated_transaction_directory(stage)
        for backup in generated_backups.values():
            _remove_validated_transaction_directory(backup)
        return True

    backups = sorted(generated_backups.values())
    if not backups:
        for stage in generated_stages.values():
            _validate_disposable_stage(stage, study_dir)
        for stage in generated_stages.values():
            _remove_validated_transaction_directory(stage)
        return bool(generated_stages)
    if len(backups) != 1:
        raise ValueError("Multiple interrupted artifact backups exist; inspect them before retrying the build")
    backup = backups[0]
    _validate_exact_artifact_directory(
        backup,
        study_dir,
        "Interrupted artifact backup must contain exactly the three regular generated artifacts",
    )
    token = _transaction_token(backup, backup_prefix)
    if token is None:  # Defensive: backups is sourced from generated_backups.
        raise ValueError("Interrupted artifact backup has an invalid transaction token")
    stage = study_dir / f"{stage_prefix}{token}"
    if stage.exists() or is_link_or_reparse(stage):
        _validate_exact_artifact_directory(
            stage,
            study_dir,
            "Matching interrupted artifact stage is not an exact generated-artifact set",
        )
    unmatched_generated_stages = [
        candidate
        for candidate_token, candidate in generated_stages.items()
        if candidate_token != token
    ]
    for candidate in unmatched_generated_stages:
        _validate_disposable_stage(candidate, study_dir)
    os.replace(backup, artifacts_dir)
    if stage.exists() or is_link_or_reparse(stage):
        _remove_validated_transaction_directory(stage)
    for candidate in unmatched_generated_stages:
        _remove_validated_transaction_directory(candidate)
    return True


def recover_interrupted_artifact_swap(study_dir: Path) -> bool:
    """Recover a bounded artifact transaction under the study writer lock."""
    with StudyBuildLock(study_dir):
        return _recover_interrupted_artifact_swap_unlocked(study_dir)


def _build_artifacts_unlocked(study_dir: Path) -> tuple[dict[str, Any], int]:
    _recover_interrupted_artifact_swap_unlocked(study_dir)
    report, context = analyze(study_dir)
    if report.get("status") == "fail":
        return report, 1
    contents = artifact_contents(report, context)
    artifacts_dir = safe_artifacts_dir(study_dir, create=False)

    # Build the complete set beside the live directory, then switch directories.
    # Per-file atomic replacement can still leave a mixed generation when the
    # second or third write fails. A staged set keeps the previous generation
    # untouched until every new artifact has been written and fsynced.
    expected_names = set(contents)
    replacing_existing = artifacts_dir.exists()
    if replacing_existing:
        actual_entries, actual_overflow = bounded_directory_entries(artifacts_dir, len(expected_names))
        actual_names = {path.name for path in actual_entries}
        if actual_overflow or actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            unexpected = sorted(actual_names - expected_names) + (["<additional entries>"] if actual_overflow else [])
            raise ValueError(
                "Refusing to replace a non-exact artifact set "
                f"(missing={missing}, unexpected={unexpected}); preserve or remove it explicitly first"
            )

    while True:
        transaction_token = secrets.token_hex(16)
        stage_dir = study_dir / f".csr-artifacts-stage-{transaction_token}"
        backup_dir = study_dir / f".csr-artifacts-backup-{transaction_token}"
        try:
            stage_dir.mkdir()
        except FileExistsError:  # pragma: no cover - cryptographic collision or concurrent creator
            continue
        if backup_dir.exists() or is_link_or_reparse(backup_dir):  # pragma: no cover - collision or concurrent creator
            stage_dir.rmdir()
            continue
        break
    previous_moved = False
    new_installed = False
    try:
        # audit.json remains last within the staged generation and acts as its
        # commit marker for readers that inspect the directory after the swap.
        for name in ARTIFACT_NAMES:
            atomic_write_text(stage_dir / name, contents[name])
        _validate_exact_artifact_directory(
            stage_dir,
            study_dir,
            "Completed artifact stage must contain exactly the three regular generated artifacts",
        )

        if replacing_existing:
            os.replace(artifacts_dir, backup_dir)
            previous_moved = True
            try:
                os.replace(stage_dir, artifacts_dir)
                _validate_exact_artifact_directory(
                    artifacts_dir,
                    study_dir,
                    "Installed artifact directory must contain exactly the three regular generated artifacts",
                )
                new_installed = True
            except BaseException:
                # Restore only when installation failed before recreating the
                # live path. If post-install validation failed, preserve both
                # the suspect live set and exact backup for explicit recovery.
                if not artifacts_dir.exists() and not is_link_or_reparse(artifacts_dir):
                    os.replace(backup_dir, artifacts_dir)
                    previous_moved = False
                raise
        else:
            os.replace(stage_dir, artifacts_dir)
            _validate_exact_artifact_directory(
                artifacts_dir,
                study_dir,
                "Installed artifact directory must contain exactly the three regular generated artifacts",
            )
            new_installed = True
    finally:
        if stage_dir.exists() or is_link_or_reparse(stage_dir):
            try:
                _validate_disposable_stage(stage_dir, study_dir)
                _remove_validated_transaction_directory(stage_dir)
            except ValueError:
                # Preserve any unexpected or replaced path for manual review.
                pass
        if new_installed and previous_moved and (backup_dir.exists() or is_link_or_reparse(backup_dir)):
            _validate_exact_artifact_directory(
                artifacts_dir,
                study_dir,
                "Installed artifact directory changed before backup cleanup",
            )
            _validate_exact_artifact_directory(
                backup_dir,
                study_dir,
                "Artifact backup changed before cleanup",
            )
            _remove_validated_transaction_directory(backup_dir)
    committed = strict_json_loads(contents["audit.json"])
    return committed, 0


def build_artifacts(study_dir: Path) -> tuple[dict[str, Any], int]:
    """Analyze and atomically install artifacts under the study writer lock."""
    with StudyBuildLock(study_dir):
        return _build_artifacts_unlocked(study_dir)


def audit_artifacts(study_dir: Path, report: dict[str, Any], context: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    artifacts_dir = safe_artifacts_dir(study_dir, create=False)
    expected = artifact_contents(report, context)
    if not artifacts_dir.exists():
        return [Issue("error", "MISSING_ARTIFACT", "artifacts", "Run build before the final audit.")]
    actual_entries, actual_overflow = bounded_directory_entries(artifacts_dir, len(expected))
    actual_names = {path.name for path in actual_entries}
    extra = sorted(actual_names - expected.keys())
    for name in extra:
        issues.append(Issue("error", "EXTRA_ARTIFACT", f"artifacts/{name}", "Generated artifact directory contains an unexpected entry."))
    if actual_overflow:
        issues.append(
            Issue(
                "error",
                "EXTRA_ARTIFACT",
                "artifacts",
                f"Generated artifact directory contains more than the expected {len(expected)} entries; scan stopped at the bound.",
            )
        )
    for name, expected_content in expected.items():
        path = artifacts_dir / name
        if not path.is_file() or is_link_or_reparse(path):
            issues.append(Issue("error", "MISSING_ARTIFACT", f"artifacts/{name}", "Run build to regenerate artifacts."))
            continue
        expected_bytes = expected_content.encode("utf-8")
        try:
            if path.stat().st_size != len(expected_bytes):
                issues.append(Issue("error", "MODIFIED_ARTIFACT", f"artifacts/{name}", "Artifact byte length does not match a fresh deterministic build."))
                continue
            with path.open("rb") as artifact_stream:
                actual_bytes = artifact_stream.read(len(expected_bytes) + 1)
            actual_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            issues.append(Issue("error", "BAD_ARTIFACT", f"artifacts/{name}", "Generated artifact is not UTF-8."))
            continue
        if actual_bytes != expected_bytes:
            issues.append(Issue("error", "MODIFIED_ARTIFACT", f"artifacts/{name}", "Artifact bytes do not match a fresh deterministic build."))
    return issues


def terminal_safe(value: Any, *, encoding: str | None = None) -> str:
    text = str(value)
    output_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    result: list[str] = []
    for character in text:
        codepoint = ord(character)
        safe = (
            codepoint >= 32
            and not 127 <= codepoint <= 159
            and codepoint not in {0x061C, 0x200E, 0x200F}
            and not (0x202A <= codepoint <= 0x202E or 0x2066 <= codepoint <= 0x2069)
        )
        if safe:
            try:
                character.encode(output_encoding)
            except (LookupError, UnicodeEncodeError):
                safe = False
        if safe:
            result.append(character)
        elif codepoint <= 0xFFFF:
            result.append(f"\\u{codepoint:04x}")
        else:
            result.append(f"\\U{codepoint:08x}")
    return "".join(result)


def print_report(report: dict[str, Any], *, json_output: bool = False, strict: bool = False) -> int:
    issues = report.get("issues", [])
    errors = sum(issue.get("severity") == "error" for issue in issues)
    warnings = sum(issue.get("severity") == "warning" for issue in issues)
    if json_output:
        print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))
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
    raw_study_id = args.study_id or ascii_casefold(study_dir.name).replace(" ", "-")
    study_id = re.sub(r"[^a-z0-9._-]+", "-", raw_study_id).strip("-._")[:80]
    if not STUDY_ID_RE.fullmatch(study_id):
        raise ValueError("study ID must contain 3-80 lowercase letters, digits, dots, underscores, or hyphens")
    if args.as_of:
        if not DATE_RE.fullmatch(args.as_of):
            raise ValueError("--as-of must be an ISO date in YYYY-MM-DD form")
        try:
            as_of_date = date.fromisoformat(args.as_of)
        except ValueError as exc:
            raise ValueError("--as-of must be an ISO date in YYYY-MM-DD form") from exc
    else:
        as_of_date = date.today()
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
        "recommendation": {"text": INIT_RECOMMENDATION, "signal_ids": [], "caveats": []},
        "next_tests": [],
        "coverage_notes": [],
        "stop_reason": INIT_STOP_REASON,
    }
    ignore_path = study_dir / ".gitignore"
    if ignore_path.exists() or is_link_or_reparse(ignore_path):
        try:
            ignore_bytes = read_regular_file_bounded(ignore_path, MAX_GITIGNORE_BYTES, "Study .gitignore")
        except BoundedFileTooLargeError as exc:
            raise ValueError(f"Study .gitignore exceeds the {MAX_GITIGNORE_BYTES}-byte safety limit") from exc
        except (FileNotFoundError, UnsafeFileReadError, OSError) as exc:
            raise ValueError(f"Refusing unsafe study ignore file: {ignore_path}") from exc
        try:
            ignore_text = ignore_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Study .gitignore must be UTF-8 encoded") from exc
    else:
        ignore_text = ""
    required_ignore_patterns = (
        ".author-key",
        ".csr-build.lock",
        ".csr-*.tmp",
        ".csr-artifacts-stage-*/",
        ".csr-artifacts-backup-*/",
    )
    # Gitignore is last-match-wins. Append every protective rule as the final
    # block even when an earlier positive exists, so an existing negation such
    # as !.author-key cannot expose the secret created below.
    separator = "" if not ignore_text or ignore_text.endswith("\n") else "\n"
    protected_ignore_text = (
        ignore_text
        + separator
        + "# Private key and interrupted-write/recovery files; never commit or publish.\n"
        + "\n".join(required_ignore_patterns)
        + "\n"
    )
    if len(protected_ignore_text.encode("utf-8")) > MAX_GITIGNORE_BYTES:
        raise ValueError(f"Study .gitignore cannot fit the required protective block within {MAX_GITIGNORE_BYTES} bytes")
    atomic_write_text(ignore_path, protected_ignore_text)
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
    author_parser.add_argument("--platform", required=True, help="Observed account platform (for example reddit or github).")

    def author_key(args: argparse.Namespace) -> int:
        # Native Windows pipelines and Python can otherwise disagree about the
        # active code page. Treat stdin as an explicit UTF-8 byte contract so
        # the same Unicode handle produces the same study-local key on every OS.
        if hasattr(sys.stdin, "buffer"):
            encoded = sys.stdin.buffer.read(MAX_AUTHOR_HANDLE_BYTES + 1)
            if len(encoded) > MAX_AUTHOR_HANDLE_BYTES:
                raise ValueError(f"author-key stdin exceeds {MAX_AUTHOR_HANDLE_BYTES} UTF-8 bytes")
            try:
                raw = strip_pinned_whitespace(encoded.decode("utf-8-sig"))
            except UnicodeDecodeError as exc:
                raise ValueError("author-key stdin must be UTF-8 encoded") from exc
        else:  # Supports embedded/test text streams without a buffer.
            text = sys.stdin.read(MAX_AUTHOR_HANDLE_BYTES + 1)
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("author-key stdin must be valid UTF-8 text") from exc
            if len(encoded) > MAX_AUTHOR_HANDLE_BYTES:
                raise ValueError(f"author-key stdin exceeds {MAX_AUTHOR_HANDLE_BYTES} UTF-8 bytes")
            raw = strip_pinned_whitespace(text)
        if not raw:
            raise ValueError("author-key expects a non-empty handle on standard input")
        if DISALLOWED_CONTROL_RE.search(raw) or any(character in raw for character in "\r\n\t"):
            raise ValueError("author-key handle contains a disallowed control character")
        platform = canonical_platform(args.platform)
        if not platform or len(platform) > 100 or DISALLOWED_CONTROL_RE.search(platform) or BIDI_FORMAT_RE.search(platform):
            raise ValueError("author-key --platform must be a short, non-empty platform label")
        study_dir = ensure_study_dir(Path(args.study_dir))
        secret_path = study_dir / ".author-key"
        try:
            secret_bytes = read_regular_file_bounded(secret_path, 66, ".author-key")
        except FileNotFoundError as exc:
            raise ValueError("study does not contain the private .author-key created by init") from exc
        except BoundedFileTooLargeError as exc:
            raise ValueError(".author-key is malformed") from exc
        except (UnsafeFileReadError, OSError) as exc:
            raise ValueError("study does not contain a safe private .author-key created by init") from exc
        if len(secret_bytes) not in {64, 65, 66}:
            raise ValueError(".author-key is malformed")
        if secret_bytes.endswith(b"\r\n"):
            encoded_secret = secret_bytes[:-2]
        elif secret_bytes.endswith(b"\n"):
            encoded_secret = secret_bytes[:-1]
        else:
            encoded_secret = secret_bytes
        if not re.fullmatch(rb"[0-9a-f]{64}", encoded_secret):
            raise ValueError(".author-key is malformed")
        secret = bytes.fromhex(encoded_secret.decode("ascii"))
        if len(secret) != 32:
            raise ValueError(".author-key is malformed")
        normalized = normalized_label(raw)
        message = ("csr-author-key-v1\0" + platform + "\0" + normalized).encode("utf-8")
        digest = hmac.new(secret, message, hashlib.sha256).hexdigest()[:16]
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
        print(
            terminal_safe(
                f"error: {exc}",
                encoding=getattr(sys.stderr, "encoding", None) or "utf-8",
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
