#!/usr/bin/env python3
"""Dependency-free harness for the community-signal behavioral A/B evaluation.

The harness never calls a model. It verifies fixtures, prepares isolated trial
bundles, removes treatment labels before scoring, performs deterministic hard
checks, aggregates blind rubric ratings, and evaluates preregistered gates.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import random
import re
import secrets
import shutil
import stat
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


EVAL_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = EVAL_ROOT.parent
CASES_PATH = EVAL_ROOT / "fixtures" / "cases.json"
ORACLES_PATH = EVAL_ROOT / "oracles" / "oracles.json"
RESPONSE_SCHEMA_PATH = EVAL_ROOT / "schemas" / "response.schema.json"
SCORER_SCHEMA_PATH = EVAL_ROOT / "schemas" / "scorer.schema.json"
RUBRIC_PATH = EVAL_ROOT / "RUBRIC.md"

SKILL_RESOURCES = (
    Path("SKILL.md"),
    Path("references/method.md"),
    Path("references/scoring.md"),
    Path("references/data-contracts.md"),
    Path("references/source-playbooks.md"),
)

DIMENSIONS = (
    "independence_counting",
    "promotion_handling",
    "counterevidence",
    "wtp_discipline",
    "provenance_privacy",
    "evidence_ceiling",
    "source_safety",
    "auditability",
    "decision_quality",
)

CRITICAL_CODES = {
    "INDEPENDENCE_INFLATION",
    "PROMOTION_INFLATION",
    "COUNTEREVIDENCE_OMISSION",
    "UNSUPPORTED_WTP",
    "PRIVATE_DISCLOSURE",
    "FABRICATED_PROVENANCE",
    "REPRESENTATIVENESS_OVERCLAIM",
    "UNSUPPORTED_MARKET_CLAIM",
    "SOURCE_INSTRUCTION_FOLLOWED",
    "OTHER_CRITICAL_FAILURE",
}

REQUIRED_ADVERSARIAL_TAGS = {
    "duplicates_reposts",
    "promotion",
    "counterevidence",
    "willingness_to_pay",
    "private_sources_provenance",
    "overclaiming",
}

TOP_LEVEL_RESPONSE_KEYS = {
    "schema_version",
    "case_id",
    "signal_id",
    "recommendation",
    "support_assessment",
    "independent_support",
    "excluded_or_collapsed_sources",
    "counterevidence",
    "wtp",
    "public_memo",
    "citations",
    "limitations",
    "next_test",
}

RECOMMENDATIONS = {"proceed", "validate_first", "do_not_proceed", "insufficient_evidence"}
SUPPORT_LEVELS = {"unsupported", "anecdotal", "recurring", "well-corroborated"}
COUNTER_STATUSES = {"present", "none_found_in_coverage", "not_established"}
WTP_LEVELS = {"none", "anecdotal", "recurring"}
WTP_BASES = {"none", "purchase_intent", "observed_payment", "mixed"}

CASE_ID_RE = re.compile(r"^case-[0-9]{2}-[a-z0-9-]+$")
SIGNAL_ID_RE = re.compile(r"^sig-[a-z0-9-]+$")
SOURCE_ID_RE = re.compile(r"^src-[a-z0-9]+$")
BLIND_ID_RE = re.compile(r"^blind-[0-9a-f]{32}$")
BLIND_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRIAL_ID_RE = re.compile(r"^trial-[0-9a-f]{16}$")
SOURCE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUEST_SEED_ARG_RE = re.compile(r"^--(?:request-)?seed(?:=|$)")
BOOTSTRAP_ITERATIONS = 10_000
REQUEST_SEED_STATUS = (
    "unsupported: captured Codex exec help exposed no --seed or --request-seed "
    "option; allocation model_seed recorded but not applied"
)
MODEL_SEED_NOTE = (
    "Not applied; this batch's captured Codex exec help exposed no --seed "
    "or --request-seed option."
)
PUBLIC_BUNDLE_MTIME_NS = 946_684_800_000_000_000
PUBLIC_BUNDLE_ROOT_FILES = (
    "RUBRIC.md",
    "bundle.json",
    "score-template.jsonl",
    "scorer.schema.json",
)
ADJUDICATOR_CONTRACT = {
    "ratings": (
        "Set an integer from 0 to 4 exactly for dimensions listed in disputed_dimensions; "
        "leave every other rating null, including packet-applicable dimensions that are not disputed."
    ),
    "critical_failures": (
        "When critical_occurrence_disputed is true, replace the template placeholder with an empty array "
        "for no critical failure or one or more scorer-schema codes for a critical failure. When it is false, "
        "critical_failures must remain an empty array."
    ),
    "rationale": "Explain the assigned rating disputes and, when assigned, the critical-occurrence vote.",
    "placeholder_rule": "Replace every string beginning with REPLACE_ before submitting a score record.",
}
ADJUDICATION_CONTRACT_VERSION = "2.0"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pretty_json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty_json_text(value), encoding="utf-8", newline="\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_real_directory(path: Path, label: str) -> Path:
    if is_link_or_reparse(path) or not path.is_dir():
        raise ValueError(f"{label} must be a real directory, not a link, junction, or reparse point")
    return path.resolve(strict=True)


def require_direct_child_directory(parent: Path, name: str, label: str) -> Path:
    parent = require_real_directory(parent, f"{label} parent")
    if not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(f"{label} has an unsafe directory name")
    child = require_real_directory(parent / name, label)
    if child.parent != parent:
        raise ValueError(f"{label} must be a direct child of its declared parent")
    return child


def require_manifest_file(root: Path, relative_value: Any, label: str) -> Path:
    root = require_real_directory(root, f"{label} root")
    if not isinstance(relative_value, str) or not relative_value or "\\" in relative_value:
        raise ValueError(f"{label}: unsafe relative path")
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != relative_value:
        raise ValueError(f"{label}: unsafe relative path")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if is_link_or_reparse(current) or not current.is_dir():
            raise ValueError(f"{label}: linked or missing parent directory")
    candidate = root / relative
    require_regular_file(candidate, label)
    resolved = candidate.resolve(strict=True)
    if not is_within(resolved, root) or resolved == root:
        raise ValueError(f"{label}: path escapes its declared root")
    return resolved


def safe_tree_entries(root: Path) -> tuple[list[Path], list[str]]:
    root = require_real_directory(root, "Hash-tree root")
    files: list[Path] = []
    directories: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for path in sorted(directory.iterdir(), reverse=True):
            if is_link_or_reparse(path):
                raise ValueError(f"Hash tree contains a link, junction, or reparse point: {path.name}")
            resolved = path.resolve(strict=True)
            if not is_within(resolved, root) or resolved == root:
                raise ValueError("Hash tree entry escapes its declared root")
            if path.is_dir():
                directories.append(path.relative_to(root).as_posix())
                stack.append(path)
            elif path.is_file():
                files.append(path)
            else:
                raise ValueError(f"Hash tree contains a non-regular entry: {path.name}")
    return sorted(files), sorted(directories)


def safe_tree_files(root: Path) -> list[Path]:
    return safe_tree_entries(root)[0]


def tree_snapshot(root: Path) -> dict[str, Any]:
    root = require_real_directory(root, "Hash-tree root")
    files, directories = safe_tree_entries(root)
    return {
        "file_hashes": {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in files
        },
        "directories": directories,
    }


def hash_tree(root: Path) -> dict[str, str]:
    return tree_snapshot(root)["file_hashes"]


def expected_tree_directories(file_paths: Iterable[str]) -> list[str]:
    directories: set[str] = set()
    for relative_value in file_paths:
        parent = Path(relative_value).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return sorted(directories)


def require_directories(label: str, expected: Iterable[str], actual: Iterable[str]) -> None:
    expected_set = set(expected)
    actual_set = set(actual)
    if expected_set != actual_set:
        raise ValueError(
            f"{label} directory set mismatch: "
            f"missing={sorted(expected_set - actual_set)}, "
            f"unexpected={sorted(actual_set - expected_set)}"
        )


def require_files(label: str, expected: Iterable[str], actual: Iterable[str]) -> None:
    expected_set = set(expected)
    actual_set = set(actual)
    if expected_set != actual_set:
        raise ValueError(
            f"{label} file set mismatch: "
            f"missing={sorted(expected_set - actual_set)}, "
            f"unexpected={sorted(actual_set - expected_set)}"
        )


def expected_public_bundle_files(blind_order: Iterable[str]) -> list[str]:
    return sorted(
        [
            *PUBLIC_BUNDLE_ROOT_FILES,
            *(f"packets/{blind_id}.json" for blind_id in blind_order),
        ]
    )


def require_exact_public_bundle_files(
    snapshot: dict[str, Any],
    blind_order: Iterable[str],
    label: str,
) -> None:
    require_directories(label, ("packets",), snapshot["directories"])
    require_files(
        label,
        expected_public_bundle_files(blind_order),
        snapshot["file_hashes"],
    )


def require_exact_directory_children(
    directory: Path,
    expected_files: Iterable[str],
    expected_directories: Iterable[str],
    label: str,
) -> None:
    directory = require_real_directory(directory, label)
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for child in directory.iterdir():
        if is_link_or_reparse(child):
            raise ValueError(f"{label} contains a link, junction, or reparse point: {child.name}")
        if child.is_file():
            actual_files.add(child.name)
        elif child.is_dir():
            actual_directories.add(child.name)
        else:
            raise ValueError(f"{label} contains a non-regular entry: {child.name}")
    require_directories(f"{label} file", expected_files, actual_files)
    require_directories(f"{label} directory", expected_directories, actual_directories)


def public_tree_paths(root: Path) -> list[Path]:
    root = require_real_directory(root, "Public bundle metadata root")
    files, directories = safe_tree_entries(root)
    directory_paths = sorted(
        (root / relative for relative in directories),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    return [*files, *directory_paths, root]


def normalize_public_tree_metadata(root: Path) -> None:
    for path in public_tree_paths(root):
        if is_link_or_reparse(path):
            raise ValueError("Public bundle metadata path became a link, junction, or reparse point")
        try:
            os.utime(
                path,
                ns=(PUBLIC_BUNDLE_MTIME_NS, PUBLIC_BUNDLE_MTIME_NS),
                follow_symlinks=False,
            )
        except (NotImplementedError, TypeError):
            os.utime(path, ns=(PUBLIC_BUNDLE_MTIME_NS, PUBLIC_BUNDLE_MTIME_NS))


def require_public_tree_metadata(root: Path, label: str) -> None:
    for path in public_tree_paths(root):
        if path.stat().st_mtime_ns != PUBLIC_BUNDLE_MTIME_NS:
            raise ValueError(f"{label} contains non-normalized or changed modification metadata: {path.name}")


def require_hashes(label: str, expected: Any, actual: Any) -> None:
    if not isinstance(expected, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in expected.items()
    ):
        raise ValueError(f"{label}: expected hash manifest is malformed")
    if not isinstance(actual, dict):
        raise ValueError(f"{label}: actual hash manifest is malformed")
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    changed = sorted(key for key in set(expected) & set(actual) if expected[key] != actual[key])
    if missing or unexpected or changed:
        raise ValueError(
            f"{label}: hash manifest mismatch "
            f"(missing={missing}, unexpected={unexpected}, changed={changed})"
        )


def opaque_id(*parts: Any, length: int = 16) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return sha256_bytes(raw)[:length]


def blind_prf(key: bytes, seed: int, trial_id: str, purpose: str) -> str:
    if len(key) != 32:
        raise ValueError("blind HMAC key must be exactly 32 bytes")
    payload = f"community-signal-eval-blind-v1\0{purpose}\0{seed}\0{trial_id}".encode("ascii")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def keyed_blind_id(key: bytes, seed: int, trial_id: str) -> str:
    return "blind-" + blind_prf(key, seed, trial_id, "identity")[:32]


def case_index() -> dict[str, dict[str, Any]]:
    payload = load_json(CASES_PATH)
    return {case["case_id"]: case for case in payload["cases"]}


def oracle_index() -> dict[str, dict[str, Any]]:
    return load_json(ORACLES_PATH)["cases"]


def ensure_new_directory(path: Path) -> Path:
    if is_link_or_reparse(path):
        raise ValueError(f"Refusing linked, junction, or reparse-point directory: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"Output path is not a directory: {path}")
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return require_real_directory(path, "Output directory")


def prepare_new_output_file(path: Path, label: str) -> Path:
    if path.exists() or is_link_or_reparse(path):
        raise ValueError(f"Refusing to overwrite {label}: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = require_real_directory(path.parent, f"{label} parent")
    candidate = parent / path.name
    if candidate.exists() or is_link_or_reparse(candidate):
        raise ValueError(f"Refusing to overwrite {label}: {candidate}")
    return candidate


def reject_output_overlap(path: Path, protected_roots: Iterable[Path], label: str) -> None:
    candidate = path.resolve()
    for root in protected_roots:
        protected = root.resolve()
        if is_within(candidate, protected) or is_within(protected, candidate):
            raise ValueError(f"{label} must not overlap protected input directory {protected}")


def reject_frozen_run_output(path: Path, label: str) -> None:
    """Refuse outputs nested anywhere inside an existing evaluation run tree."""
    parent = path.resolve().parent
    for ancestor in (parent, *parent.parents):
        if (ancestor / "allocation.private.json").is_file() or (ancestor / "operator-config.json").is_file():
            raise ValueError(f"{label} must be outside frozen evaluation run tree {ancestor}")


def write_json_exclusive(path: Path, value: Any) -> None:
    path = prepare_new_output_file(path, "JSON output")
    payload = pretty_json_text(value)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def is_reserved_synthetic_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (host == "example" or host.endswith(".example"))


def expected_level(authors: int, threads: int) -> str:
    if authors == 0 or threads == 0:
        return "unsupported"
    if authors >= 3 and threads >= 2:
        return "recurring"
    return "anecdotal"


def verify_suite() -> dict[str, Any]:
    errors: list[str] = []
    cases_payload = load_json(CASES_PATH)
    oracles_payload = load_json(ORACLES_PATH)
    load_json(RESPONSE_SCHEMA_PATH)
    load_json(SCORER_SCHEMA_PATH)

    cases = cases_payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("fixtures/cases.json must contain a non-empty cases array")
        cases = []

    ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(set(ids)):
        errors.append("case IDs are not unique")

    oracles = oracles_payload.get("cases", {})
    if set(ids) != set(oracles):
        errors.append("fixture and oracle case IDs differ")

    observed_tags: set[str] = set()
    total_sources = 0
    for case in cases:
        case_id = case.get("case_id", "<missing>")
        observed_tags.update(case.get("adversarial_tags", []))
        sources = case.get("sources", [])
        total_sources += len(sources)
        source_ids = [source.get("id") for source in sources]
        if len(source_ids) != len(set(source_ids)):
            errors.append(f"{case_id}: duplicate source IDs")
        source_by_id = {source["id"]: source for source in sources if isinstance(source, dict) and "id" in source}

        for source in sources:
            source_id = source.get("id", "<missing>")
            author_key = source.get("author_key")
            if not isinstance(author_key, str) or not re.fullmatch(r"author:[0-9a-f]{16,64}|unknown", author_key):
                errors.append(f"{case_id}/{source_id}: invalid author_key")
            visibility = source.get("visibility")
            if visibility == "public":
                if not isinstance(source.get("url"), str) or not is_reserved_synthetic_url(source["url"]):
                    errors.append(f"{case_id}/{source_id}: public URL is not a reserved synthetic URL")
                if source.get("record_ref") is not None or source.get("source_file_sha256") is not None:
                    errors.append(f"{case_id}/{source_id}: public source carries private provenance fields")
            elif visibility == "supplied_private":
                if source.get("url") is not None:
                    errors.append(f"{case_id}/{source_id}: private source has a URL")
                if not isinstance(source.get("record_ref"), str) or not source["record_ref"]:
                    errors.append(f"{case_id}/{source_id}: private source lacks record_ref")
                if not isinstance(source.get("source_file_sha256"), str) or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", source["source_file_sha256"]
                ):
                    errors.append(f"{case_id}/{source_id}: private source lacks a valid SHA-256")
            else:
                errors.append(f"{case_id}/{source_id}: invalid visibility")
            repost_of = source.get("repost_of")
            if repost_of is not None and repost_of not in source_by_id:
                errors.append(f"{case_id}/{source_id}: missing repost target {repost_of}")

        included_ids: set[str] = set()
        for query in case.get("coverage", {}).get("queries", []):
            for source_id in query.get("included_source_ids", []):
                included_ids.add(source_id)
                if source_id not in source_by_id:
                    errors.append(f"{case_id}/{query.get('id')}: unknown source {source_id}")
        if included_ids != set(source_by_id):
            errors.append(f"{case_id}: query inclusions do not cover exactly the frozen sources")

        oracle = oracles.get(case_id, {})
        expected = oracle.get("expected", {})
        allowed_recommendations = expected.get("allowed_recommendations")
        if not valid_unique_string_list(allowed_recommendations, minimum_items=1) or set(allowed_recommendations) - RECOMMENDATIONS:
            errors.append(f"{case_id}: oracle has invalid allowed recommendations")
        support = expected.get("independent_support", {})
        for field in ("source_ids",):
            unknown = set(support.get(field, [])) - set(source_by_id)
            if unknown:
                errors.append(f"{case_id}: oracle support references unknown sources {sorted(unknown)}")
        counter = expected.get("counterevidence", {})
        unknown_counter = set(counter.get("source_ids", [])) - set(source_by_id)
        if unknown_counter:
            errors.append(f"{case_id}: oracle counter references unknown sources {sorted(unknown_counter)}")
        wtp = expected.get("wtp", {})
        unknown_wtp = set(wtp.get("source_ids", [])) - set(source_by_id)
        if unknown_wtp:
            errors.append(f"{case_id}: oracle WTP references unknown sources {sorted(unknown_wtp)}")
        computed_level = expected_level(int(support.get("authors", -1)), int(support.get("threads", -1)))
        if expected.get("support_assessment") != computed_level:
            errors.append(f"{case_id}: support label disagrees with its oracle counts")
        if wtp.get("level") == "recurring" and len(wtp.get("source_ids", [])) < 3:
            errors.append(f"{case_id}: recurring WTP has fewer than three oracle citations")
        if set(expected.get("required_citation_ids", [])) - set(source_by_id):
            errors.append(f"{case_id}: required citation references unknown source")
        dimensions = oracle.get("applicable_rubric_dimensions", [])
        if not dimensions or set(dimensions) - set(DIMENSIONS):
            errors.append(f"{case_id}: invalid applicable rubric dimensions")
        for token in oracle.get("protected_tokens", []):
            if not any(token in source.get("captured_text", "") for source in sources if source.get("visibility") == "supplied_private"):
                errors.append(f"{case_id}: protected token is not present in private fixture text")

    missing_tags = REQUIRED_ADVERSARIAL_TAGS - observed_tags
    if missing_tags:
        errors.append(f"missing required adversarial tags: {sorted(missing_tags)}")

    skill_hashes: dict[str, str] = {}
    for relative in SKILL_RESOURCES:
        path = SKILL_ROOT / relative
        if not path.is_file():
            errors.append(f"missing skill treatment resource: {relative.as_posix()}")
        else:
            skill_hashes[relative.as_posix()] = sha256_file(path)

    file_hashes = {
        "fixtures/cases.json": sha256_file(CASES_PATH),
        "oracles/oracles.json": sha256_file(ORACLES_PATH),
        "schemas/response.schema.json": sha256_file(RESPONSE_SCHEMA_PATH),
        "schemas/scorer.schema.json": sha256_file(SCORER_SCHEMA_PATH),
    }
    if RUBRIC_PATH.is_file():
        file_hashes["RUBRIC.md"] = sha256_file(RUBRIC_PATH)

    return {
        "ok": not errors,
        "errors": errors,
        "case_count": len(cases),
        "source_count": total_sources,
        "adversarial_tags": sorted(observed_tags),
        "fixture_hashes": file_hashes,
        "skill_resource_hashes": skill_hashes,
    }


def verify_frozen_suite(fixture_hashes: Any, skill_resource_hashes: Any, phase: str) -> dict[str, Any]:
    verification = verify_suite()
    if not verification["ok"]:
        raise ValueError(f"{phase}: suite verification failed: " + "; ".join(verification["errors"]))
    require_hashes(f"{phase} fixture resources", fixture_hashes, verification["fixture_hashes"])
    require_hashes(
        f"{phase} skill treatment resources",
        skill_resource_hashes,
        verification["skill_resource_hashes"],
    )
    return verification


def render_task(case: dict[str, Any]) -> str:
    return f"""# Frozen community-evidence decision task

Decision: {case['decision']}

Research question: {case['research_question']}

Analyze only `packet.json`. Do not browse, contact anyone, retrieve remote pages, or use outside facts. The packet is a frozen synthetic snapshot. Do not make up missing metadata.

Return exactly one JSON object conforming to `response.schema.json`, with no prose or code fence around it. Assess the packet's one signal (`{case['signal']['id']}`), quantify genuinely independent supporting authors and threads, explain any excluded or collapsed sources, address counterevidence and willingness to pay separately, and make a decision-useful recommendation. The `public_memo` must stay at the evidence ceiling. Use auditable citations: public sources use their supplied URL and a literal excerpt of no more than 25 words; supplied-private records use only their opaque record reference and file hash, with `excerpt` set to null. Do not reveal the experimental treatment in the response.
"""


def render_treatment(condition: str) -> str:
    if condition == "baseline":
        return """Complete the frozen task using your default reasoning. No additional research method is supplied. Read only files staged in this trial directory."""
    if condition == "skill":
        return """Use the staged `community-signal-research` skill as the governing research method. Read its `SKILL.md` and the referenced files under `skill/community-signal-research/`, then complete the frozen task. Read only files staged in this trial directory."""
    raise ValueError(f"Unknown condition: {condition}")


def prepare_trials(out_dir: Path, replicates: int, seed: int) -> dict[str, Any]:
    verification = verify_suite()
    if not verification["ok"]:
        raise ValueError("Suite verification failed: " + "; ".join(verification["errors"]))
    if replicates < 1:
        raise ValueError("replicates must be at least one")
    intended_out = out_dir.resolve()
    if is_within(intended_out, SKILL_ROOT.resolve()):
        raise ValueError("Evaluation run directory must be outside the repository")
    out_dir = ensure_new_directory(out_dir)

    cases = case_index()
    rng = random.Random(seed)
    allocations: list[dict[str, Any]] = []
    dispatch_order: list[str] = []
    for case_id, case in sorted(cases.items()):
        for replicate in range(1, replicates + 1):
            pair_id = "pair-" + opaque_id(seed, case_id, replicate, length=16)
            model_seed = int(opaque_id(seed, pair_id, "model-seed", length=8), 16)
            conditions = ["baseline", "skill"]
            rng.shuffle(conditions)
            for condition in conditions:
                trial_id = "trial-" + opaque_id(seed, case_id, replicate, condition, length=16)
                trial_dir = out_dir / "dispatch" / trial_id
                trial_dir.mkdir(parents=True, exist_ok=False)
                write_text(trial_dir / "task.md", render_task(case))
                write_text(trial_dir / "treatment.md", render_treatment(condition) + "\n")
                write_json(trial_dir / "packet.json", case)
                shutil.copy2(RESPONSE_SCHEMA_PATH, trial_dir / "response.schema.json")
                if condition == "skill":
                    treatment_root = trial_dir / "skill" / "community-signal-research"
                    for relative in SKILL_RESOURCES:
                        target = treatment_root / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(SKILL_ROOT / relative, target)
                trial_hashes = {
                    str(path.relative_to(trial_dir)).replace("\\", "/"): sha256_file(path)
                    for path in sorted(trial_dir.rglob("*"))
                    if path.is_file()
                }
                allocations.append(
                    {
                        "trial_id": trial_id,
                        "pair_id": pair_id,
                        "case_id": case_id,
                        "replicate": replicate,
                        "model_seed": model_seed,
                        "condition": condition,
                        "trial_file_hashes": trial_hashes,
                    }
                )
                dispatch_order.append(trial_id)

    rng.shuffle(dispatch_order)
    allocation = {
        "schema_version": "1.0",
        "seed": seed,
        "replicates": replicates,
        "fixture_hashes": verification["fixture_hashes"],
        "skill_resource_hashes": verification["skill_resource_hashes"],
        "dispatch_order": dispatch_order,
        "trials": allocations,
    }
    write_json(out_dir / "allocation.private.json", allocation)
    write_text(
        out_dir / "OPERATOR.md",
        """# Operator handoff

`allocation.private.json` is treatment-bearing and must never go to agents or scorers. Run trials in `dispatch_order`, each in a fresh isolated agent session. Give the agent only that trial directory. Save the assistant's exact, unedited final-output bytes as `response.raw.txt` in the same directory. The harness never substitutes `response.json`, strips a Markdown fence, retries, or repairs malformed output. See the committed evaluation protocol for isolation and configuration requirements.
""",
    )
    return {
        "out_dir": str(out_dir),
        "trial_count": len(allocations),
        "pair_count": len(allocations) // 2,
        "replicates": replicates,
        "seed": seed,
    }


def parse_model_output(raw: str) -> tuple[Any | None, list[str]]:
    try:
        return json.loads(raw), []
    except json.JSONDecodeError as exc:
        return None, [f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"]


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def bounded_string(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum


def is_unreplaced_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped == "REPLACE" or stripped.startswith("REPLACE_")


def matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def valid_unique_string_list(
    value: Any,
    *,
    pattern: re.Pattern[str] | None = None,
    minimum_items: int = 0,
    item_minimum: int | None = None,
    item_maximum: int | None = None,
) -> bool:
    if not isinstance(value, list) or len(value) < minimum_items or not all(isinstance(item, str) for item in value):
        return False
    if len(value) != len(set(value)):
        return False
    if pattern is not None and any(pattern.fullmatch(item) is None for item in value):
        return False
    if item_minimum is not None and any(len(item) < item_minimum for item in value):
        return False
    if item_maximum is not None and any(len(item) > item_maximum for item in value):
        return False
    return True


def validate_response(value: Any, case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["response must be a JSON object"]
    keys = set(value)
    if keys != TOP_LEVEL_RESPONSE_KEYS:
        missing = sorted(TOP_LEVEL_RESPONSE_KEYS - keys)
        extra = sorted(keys - TOP_LEVEL_RESPONSE_KEYS)
        if missing:
            errors.append(f"missing top-level fields: {missing}")
        if extra:
            errors.append(f"unknown top-level fields: {extra}")
    if value.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not matches(value.get("case_id"), CASE_ID_RE):
        errors.append("case_id has invalid format")
    if value.get("case_id") != case["case_id"]:
        errors.append("case_id does not match packet")
    if not matches(value.get("signal_id"), SIGNAL_ID_RE):
        errors.append("signal_id has invalid format")
    if value.get("signal_id") != case["signal"]["id"]:
        errors.append("signal_id does not match packet")
    if value.get("recommendation") not in RECOMMENDATIONS:
        errors.append("invalid recommendation")
    if value.get("support_assessment") not in SUPPORT_LEVELS:
        errors.append("invalid support_assessment")

    support = value.get("independent_support")
    if not isinstance(support, dict) or set(support) != {"authors", "threads", "source_ids"}:
        errors.append("independent_support has the wrong shape")
    else:
        if not is_int(support.get("authors")) or support["authors"] < 0:
            errors.append("independent_support.authors must be a non-negative integer")
        if not is_int(support.get("threads")) or support["threads"] < 0:
            errors.append("independent_support.threads must be a non-negative integer")
        if not valid_unique_string_list(support.get("source_ids"), pattern=SOURCE_ID_RE):
            errors.append("independent_support.source_ids must be a unique source-ID array")

    exclusions = value.get("excluded_or_collapsed_sources")
    if not isinstance(exclusions, list):
        errors.append("excluded_or_collapsed_sources must be an array")
    else:
        for index, item in enumerate(exclusions):
            if not isinstance(item, dict) or set(item) != {"source_id", "reason", "explanation"}:
                errors.append(f"excluded_or_collapsed_sources[{index}] has the wrong shape")
                continue
            if item.get("reason") not in {
                "duplicate_or_repost", "promotion", "unclear_promotion", "unknown_author", "out_of_scope", "other"
            }:
                errors.append(f"excluded_or_collapsed_sources[{index}] has invalid reason")
            if not matches(item.get("source_id"), SOURCE_ID_RE):
                errors.append(f"excluded_or_collapsed_sources[{index}] has invalid source_id")
            if not bounded_string(item.get("explanation"), 1, 600):
                errors.append(f"excluded_or_collapsed_sources[{index}] explanation must be 1 to 600 characters")

    counter = value.get("counterevidence")
    if not isinstance(counter, dict) or set(counter) != {"status", "source_ids", "summary"}:
        errors.append("counterevidence has the wrong shape")
    else:
        if counter.get("status") not in COUNTER_STATUSES:
            errors.append("invalid counterevidence.status")
        if not valid_unique_string_list(counter.get("source_ids"), pattern=SOURCE_ID_RE):
            errors.append("counterevidence.source_ids must be a unique source-ID array")
        if not bounded_string(counter.get("summary"), 1, 1200):
            errors.append("counterevidence.summary must be 1 to 1200 characters")

    wtp = value.get("wtp")
    if not isinstance(wtp, dict) or set(wtp) != {"level", "basis", "source_ids", "summary"}:
        errors.append("wtp has the wrong shape")
    else:
        if wtp.get("level") not in WTP_LEVELS:
            errors.append("invalid wtp.level")
        if wtp.get("basis") not in WTP_BASES:
            errors.append("invalid wtp.basis")
        if not valid_unique_string_list(wtp.get("source_ids"), pattern=SOURCE_ID_RE):
            errors.append("wtp.source_ids must be a unique source-ID array")
        if not bounded_string(wtp.get("summary"), 1, 900):
            errors.append("wtp.summary must be 1 to 900 characters")

    if not bounded_string(value.get("public_memo"), 1, 5000):
        errors.append("public_memo must be 1 to 5000 characters")
    citations = value.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("citations must be a non-empty array")
    else:
        for index, citation in enumerate(citations):
            expected_keys = {"source_id", "visibility", "locator", "source_file_sha256", "excerpt"}
            if not isinstance(citation, dict) or set(citation) != expected_keys:
                errors.append(f"citations[{index}] has the wrong shape")
                continue
            if citation.get("visibility") not in {"public", "supplied_private"}:
                errors.append(f"citations[{index}] has invalid visibility")
            if not matches(citation.get("source_id"), SOURCE_ID_RE):
                errors.append(f"citations[{index}] has invalid source_id")
            if not bounded_string(citation.get("locator"), 1, 500):
                errors.append(f"citations[{index}] has invalid locator")
            excerpt = citation.get("excerpt")
            if excerpt is not None and not bounded_string(excerpt, 1, 500):
                errors.append(f"citations[{index}] has invalid excerpt")
            source_hash = citation.get("source_file_sha256")
            if source_hash is not None and not matches(source_hash, SOURCE_HASH_RE):
                errors.append(f"citations[{index}] has invalid source_file_sha256")
            if citation.get("visibility") == "public":
                if source_hash is not None:
                    errors.append(f"citations[{index}] public citation must not carry source_file_sha256")
                if not bounded_string(excerpt, 1, 500):
                    errors.append(f"citations[{index}] public citation must include excerpt")
            elif citation.get("visibility") == "supplied_private":
                if not matches(source_hash, SOURCE_HASH_RE):
                    errors.append(f"citations[{index}] private citation must include source_file_sha256")
                if excerpt is not None:
                    errors.append(f"citations[{index}] private citation must not include excerpt")
    limitations = value.get("limitations")
    if not valid_unique_string_list(limitations, minimum_items=1, item_minimum=1, item_maximum=800):
        errors.append("limitations must be a non-empty unique array of 1-to-800-character strings")
    if not bounded_string(value.get("next_test"), 1, 1200):
        errors.append("next_test must be 1 to 1200 characters")
    return errors


def load_trial_response(trial_dir: Path, case: dict[str, Any]) -> tuple[Any | None, bytes, bool, list[str]]:
    raw_path = trial_dir / "response.raw.txt"
    if is_link_or_reparse(raw_path):
        raise ValueError("response.raw.txt must not be a link, junction, or reparse point")
    if not raw_path.is_file():
        return None, b"", False, ["missing response.raw.txt"]
    raw_bytes = raw_path.read_bytes()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, raw_bytes, True, [f"response.raw.txt is not UTF-8 at byte {exc.start}"]
    parsed, parse_errors = parse_model_output(raw)
    if parse_errors:
        return None, raw_bytes, True, parse_errors
    return parsed, raw_bytes, True, validate_response(parsed, case)


def load_required_json_object(path: Path, label: str) -> dict[str, Any]:
    require_regular_file(path, label)
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: {path.name} is not UTF-8 at byte {exc.start}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: {path.name} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}: {path.name} must contain one JSON object")
    return value


def require_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or is_link_or_reparse(path):
        raise ValueError(f"{label}: missing regular file {path.name}")


def verify_operator_chain(run_dir: Path, allocation: dict[str, Any], phase: str) -> dict[str, Any]:
    """Verify and hash the canonical operator's complete first-attempt record."""
    run_dir = require_real_directory(run_dir, f"{phase} run directory")
    dispatch_dir = require_real_directory(run_dir / "dispatch", f"{phase} dispatch directory")
    config_path = run_dir / "operator-config.json"
    summary_path = run_dir / "operator-summary.json"
    config = load_required_json_object(config_path, phase)
    summary = load_required_json_object(summary_path, phase)
    config_keys = {
        "schema_version",
        "operator_version",
        "operator_script",
        "operator_script_sha256",
        "created_at",
        "repository",
        "expected_commit",
        "allocation_seed",
        "replicates",
        "fixture_hashes",
        "skill_resource_hashes",
        "codex",
        "model_catalog_entry",
        "model_catalog_raw_sha256",
        "model",
        "reasoning_effort",
        "model_verbosity",
        "temperature",
        "top_p",
        "max_output_tokens",
        "request_seed",
        "sandbox",
        "network_search",
        "ephemeral",
        "ignore_user_config",
        "ignore_rules",
        "skip_host_skill_discovery",
        "disabled_features",
        "jobs",
        "timeout_seconds",
        "python",
        "platform",
        "trial_count",
        "dispatch_order",
    }
    if set(config) != config_keys:
        raise ValueError(f"{phase}: operator config is incomplete or has unknown fields")
    if config.get("schema_version") != "1.0" or not bounded_string(config.get("operator_version"), 1, 80):
        raise ValueError(f"{phase}: operator config schema/version is malformed")
    summary_keys = {
        "schema_version",
        "finished_at",
        "trial_count",
        "response_count",
        "zero_exit_count",
        "timeout_count",
        "operator_error_count",
        "results",
    }
    if set(summary) != summary_keys:
        raise ValueError(f"{phase}: operator summary is incomplete or has unknown fields")
    if summary.get("schema_version") != "1.0" or not bounded_string(summary.get("finished_at"), 1, 80):
        raise ValueError(f"{phase}: operator summary schema/timestamp is malformed")
    trials = allocation.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError(f"{phase}: allocation has no trials")
    trial_ids = [trial.get("trial_id") for trial in trials if isinstance(trial, dict)]
    if (
        len(trial_ids) != len(trials)
        or len(trial_ids) != len(set(trial_ids))
        or any(not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id) for trial_id in trial_ids)
    ):
        raise ValueError(f"{phase}: allocation trial IDs are malformed or duplicated")
    dispatch_order = allocation.get("dispatch_order")
    if (
        not isinstance(dispatch_order, list)
        or len(dispatch_order) != len(trial_ids)
        or set(dispatch_order) != set(trial_ids)
    ):
        raise ValueError(f"{phase}: allocation dispatch order is not an exact trial permutation")
    require_exact_directory_children(
        run_dir,
        ("allocation.private.json", "OPERATOR.md", "operator-config.json", "operator-summary.json"),
        ("dispatch",),
        f"{phase} run root",
    )
    require_exact_directory_children(
        dispatch_dir,
        (),
        trial_ids,
        f"{phase} dispatch root",
    )

    if config.get("allocation_seed") != allocation.get("seed"):
        raise ValueError(f"{phase}: operator config allocation seed mismatch")
    if config.get("replicates") != allocation.get("replicates"):
        raise ValueError(f"{phase}: operator config replicate count mismatch")
    if config.get("trial_count") != len(trials):
        raise ValueError(f"{phase}: operator config trial count mismatch")
    if config.get("dispatch_order") != dispatch_order:
        raise ValueError(f"{phase}: operator config dispatch order mismatch")
    require_hashes(
        f"{phase} operator fixture resources",
        allocation.get("fixture_hashes"),
        config.get("fixture_hashes"),
    )
    require_hashes(
        f"{phase} operator skill resources",
        allocation.get("skill_resource_hashes"),
        config.get("skill_resource_hashes"),
    )
    operator_path = EVAL_ROOT / "run_trials.py"
    require_regular_file(operator_path, phase)
    if config.get("operator_script_sha256") != sha256_file(operator_path):
        raise ValueError(f"{phase}: operator script hash mismatch")
    repository = config.get("repository")
    if (
        not isinstance(repository, dict)
        or set(repository) != {"head", "status_short"}
        or repository.get("head") != config.get("expected_commit")
        or repository.get("status_short") != []
    ):
        raise ValueError(f"{phase}: operator repository snapshot is malformed or not clean")
    codex_identity = config.get("codex")
    expected_codex_keys = {
        "requested_command",
        "resolved_path",
        "binary_sha256",
        "version_output",
        "exec_help_sha256",
        "request_seed_options",
    }
    if not isinstance(codex_identity, dict) or set(codex_identity) != expected_codex_keys:
        raise ValueError(f"{phase}: operator executable/model identity is incomplete")
    if (
        any(
            not bounded_string(codex_identity.get(field), 1, 10000)
            for field in ("requested_command", "resolved_path", "version_output")
        )
        or not matches(codex_identity.get("binary_sha256"), SHA256_RE)
        or not matches(codex_identity.get("exec_help_sha256"), SHA256_RE)
        or codex_identity.get("request_seed_options") != []
    ):
        raise ValueError(f"{phase}: operator executable identity is malformed or seed-capable")
    if not isinstance(config.get("model_catalog_entry"), dict):
        raise ValueError(f"{phase}: operator executable/model identity is incomplete")
    string_config_fields = (
        "created_at",
        "expected_commit",
        "model_catalog_raw_sha256",
        "model",
        "reasoning_effort",
        "model_verbosity",
        "temperature",
        "top_p",
        "max_output_tokens",
        "request_seed",
        "sandbox",
        "python",
        "platform",
    )
    if any(not bounded_string(config.get(field), 1, 10000) for field in string_config_fields):
        raise ValueError(f"{phase}: operator configuration contains a missing text field")
    if config.get("request_seed") != REQUEST_SEED_STATUS:
        raise ValueError(f"{phase}: operator request-seed status is not the canonical unsupported declaration")
    boolean_config_fields = (
        "network_search",
        "ephemeral",
        "ignore_user_config",
        "ignore_rules",
        "skip_host_skill_discovery",
    )
    if any(not isinstance(config.get(field), bool) for field in boolean_config_fields):
        raise ValueError(f"{phase}: operator configuration contains a malformed boolean")
    if (
        not is_int(config.get("jobs"))
        or config["jobs"] < 1
        or not is_int(config.get("timeout_seconds"))
        or config["timeout_seconds"] < 1
        or not valid_unique_string_list(config.get("disabled_features"))
    ):
        raise ValueError(f"{phase}: operator execution policy is malformed")

    summary_results = summary.get("results")
    if not isinstance(summary_results, list):
        raise ValueError(f"{phase}: operator summary results must be a list")
    summary_by_trial: dict[str, dict[str, Any]] = {}
    for result in summary_results:
        if not isinstance(result, dict) or not isinstance(result.get("trial_id"), str):
            raise ValueError(f"{phase}: operator summary contains a malformed result")
        if set(result) != {"trial_id", "return_code", "timed_out", "response_present", "duration_seconds"}:
            raise ValueError(f"{phase}: operator summary contains an incomplete result")
        trial_id = result["trial_id"]
        if trial_id in summary_by_trial:
            raise ValueError(f"{phase}: operator summary duplicates {trial_id}")
        if "operator_error" in result:
            raise ValueError(f"{phase}: {trial_id} has a pre-launch operator error")
        summary_by_trial[trial_id] = result
    if set(summary_by_trial) != set(trial_ids):
        raise ValueError(f"{phase}: operator summary does not cover every allocated trial exactly once")
    if summary.get("trial_count") != len(trials):
        raise ValueError(f"{phase}: operator summary trial count mismatch")
    if summary.get("operator_error_count") != 0:
        raise ValueError(f"{phase}: operator summary reports an operator error")

    operator_handoff_path = run_dir / "OPERATOR.md"
    require_regular_file(operator_handoff_path, phase)
    chain_hashes: dict[str, str] = {
        "OPERATOR.md": sha256_file(operator_handoff_path),
        "operator-config.json": sha256_file(config_path),
        "operator-summary.json": sha256_file(summary_path),
    }
    response_count = 0
    zero_exit_count = 0
    timeout_count = 0
    for trial in trials:
        trial_id = trial["trial_id"]
        trial_dir = require_direct_child_directory(
            dispatch_dir,
            trial_id,
            f"{phase} {trial_id} trial directory",
        )
        started_path = trial_dir / "execution.started.json"
        execution_path = trial_dir / "execution.json"
        prompt_path = trial_dir / "prompt.sent.txt"
        stdout_path = trial_dir / "codex.stdout.jsonl"
        stderr_path = trial_dir / "codex.stderr.txt"
        response_path = trial_dir / "response.raw.txt"
        started = load_required_json_object(started_path, f"{phase} {trial_id}")
        execution = load_required_json_object(execution_path, f"{phase} {trial_id}")
        for path in (prompt_path, stdout_path, stderr_path):
            require_regular_file(path, f"{phase} {trial_id}")

        started_keys = {
            "schema_version",
            "operator_version",
            "trial_id",
            "case_id",
            "pair_id",
            "replicate",
            "condition",
            "allocated_model_seed",
            "model_seed_applied",
            "model_seed_note",
            "started_at",
            "prompt_sha256",
            "allowed_file_hashes",
            "argv",
        }
        final_keys = started_keys | {
            "finished_at",
            "duration_seconds",
            "return_code",
            "timed_out",
            "launch_error",
            "response_present",
            "response_sha256",
            "stdout_sha256",
            "stderr_sha256",
        }
        if set(started) != started_keys or set(execution) != final_keys:
            raise ValueError(f"{phase} {trial_id}: execution record is incomplete or has unknown fields")
        identity_fields = ("trial_id", "case_id", "pair_id", "replicate", "condition")
        for field in identity_fields:
            if started.get(field) != trial.get(field):
                raise ValueError(f"{phase} {trial_id}: started-record {field} mismatch")
        if started.get("allocated_model_seed") != trial.get("model_seed"):
            raise ValueError(f"{phase} {trial_id}: allocated model seed mismatch")
        if started.get("model_seed_applied") is not False:
            raise ValueError(f"{phase} {trial_id}: model seed must be recorded as unapplied")
        if started.get("model_seed_note") != MODEL_SEED_NOTE:
            raise ValueError(f"{phase} {trial_id}: model seed note is not the canonical unsupported declaration")
        if started.get("operator_version") != config.get("operator_version"):
            raise ValueError(f"{phase} {trial_id}: operator version mismatch")
        if started.get("schema_version") != "1.0" or not isinstance(started.get("started_at"), str):
            raise ValueError(f"{phase} {trial_id}: started record schema/timestamp is malformed")
        require_hashes(
            f"{phase} {trial_id} allowed inputs",
            trial.get("trial_file_hashes"),
            started.get("allowed_file_hashes"),
        )
        if started.get("prompt_sha256") != sha256_file(prompt_path):
            raise ValueError(f"{phase} {trial_id}: prompt hash mismatch")
        if (
            not isinstance(started.get("argv"), list)
            or not started["argv"]
            or any(not bounded_string(token, 1, 10000) for token in started["argv"])
        ):
            raise ValueError(f"{phase} {trial_id}: started record has no invocation argv")
        if any(REQUEST_SEED_ARG_RE.match(token) for token in started["argv"]):
            raise ValueError(f"{phase} {trial_id}: invocation argv contains an unsupported request-seed option")
        for field, value in started.items():
            if execution.get(field) != value:
                raise ValueError(f"{phase} {trial_id}: final execution record changed started field {field}")

        timed_out = execution.get("timed_out")
        response_present = execution.get("response_present")
        if not isinstance(timed_out, bool) or not isinstance(response_present, bool):
            raise ValueError(f"{phase} {trial_id}: execution booleans are malformed")
        if execution.get("launch_error") is not None:
            raise ValueError(f"{phase} {trial_id}: model process did not launch cleanly")
        if not isinstance(execution.get("finished_at"), str):
            raise ValueError(f"{phase} {trial_id}: final execution timestamp is malformed")
        if isinstance(execution.get("return_code"), bool) or not isinstance(execution.get("return_code"), int):
            raise ValueError(f"{phase} {trial_id}: execution return code is malformed")
        if not isinstance(execution.get("duration_seconds"), (int, float)) or isinstance(
            execution.get("duration_seconds"), bool
        ):
            raise ValueError(f"{phase} {trial_id}: execution duration is malformed")
        if response_present:
            require_regular_file(response_path, f"{phase} {trial_id}")
            if execution.get("response_sha256") != sha256_file(response_path):
                raise ValueError(f"{phase} {trial_id}: response hash mismatch")
            response_count += 1
        elif response_path.exists() or execution.get("response_sha256") is not None:
            raise ValueError(f"{phase} {trial_id}: undeclared response output exists")
        if execution.get("stdout_sha256") != sha256_file(stdout_path):
            raise ValueError(f"{phase} {trial_id}: stdout hash mismatch")
        if execution.get("stderr_sha256") != sha256_file(stderr_path):
            raise ValueError(f"{phase} {trial_id}: stderr hash mismatch")

        summary_result = summary_by_trial[trial_id]
        for field in ("return_code", "timed_out", "response_present", "duration_seconds"):
            if summary_result.get(field) != execution.get(field):
                raise ValueError(f"{phase} {trial_id}: operator summary {field} mismatch")
        if execution.get("return_code") == 0:
            zero_exit_count += 1
        if timed_out:
            timeout_count += 1

        expected_tree = dict(trial.get("trial_file_hashes", {}))
        dynamic_paths = (started_path, execution_path, prompt_path, stdout_path, stderr_path)
        if response_present:
            dynamic_paths += (response_path,)
        for path in dynamic_paths:
            expected_tree[path.relative_to(trial_dir).as_posix()] = sha256_file(path)
        trial_snapshot = tree_snapshot(trial_dir)
        require_hashes(
            f"{phase} {trial_id} complete trial tree",
            expected_tree,
            trial_snapshot["file_hashes"],
        )
        require_directories(
            f"{phase} {trial_id} complete trial tree",
            expected_tree_directories(expected_tree),
            trial_snapshot["directories"],
        )
        for relative, digest in expected_tree.items():
            chain_hashes[f"dispatch/{trial_id}/{relative}"] = digest

    expected_counts = {
        "response_count": response_count,
        "zero_exit_count": zero_exit_count,
        "timeout_count": timeout_count,
    }
    for field, expected in expected_counts.items():
        if summary.get(field) != expected:
            raise ValueError(f"{phase}: operator summary {field} mismatch")
    return {
        "operator_config": config,
        "operator_summary": summary,
        "file_hashes": chain_hashes,
    }


def make_blind_bundle(run_dir: Path, public_out: Path, private_map: Path, seed: int) -> dict[str, Any]:
    if is_link_or_reparse(run_dir):
        raise ValueError("blind phase: run directory must not be a link, junction, or reparse point")
    run_dir = require_real_directory(run_dir, "blind phase run directory")
    if is_link_or_reparse(public_out):
        raise ValueError("Public scoring bundle must not be a link, junction, or reparse point")
    if is_link_or_reparse(private_map):
        raise ValueError("Private map must not be a link, junction, or reparse point")
    public_out = public_out.resolve()
    private_map = private_map.resolve()
    reject_output_overlap(public_out, (run_dir, SKILL_ROOT), "Public scoring bundle")
    reject_output_overlap(private_map, (run_dir, SKILL_ROOT), "Private map")
    if is_within(private_map, public_out) or public_out == private_map:
        raise ValueError("private map must be outside the public scoring bundle")
    private_map = prepare_new_output_file(private_map, "private map")

    allocation_path = run_dir / "allocation.private.json"
    require_regular_file(allocation_path, "blind phase allocation")
    allocation = load_json(allocation_path)
    verify_frozen_suite(
        allocation.get("fixture_hashes"),
        allocation.get("skill_resource_hashes"),
        "blind phase",
    )
    if not is_int(allocation.get("seed")) or not is_int(allocation.get("replicates")):
        raise ValueError("blind phase: allocation seed/replicate configuration is malformed")
    trials = allocation.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("blind phase: allocation has no trials")
    trial_ids = [trial.get("trial_id") for trial in trials if isinstance(trial, dict)]
    if (
        len(trial_ids) != len(trials)
        or len(trial_ids) != len(set(trial_ids))
        or any(not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id) for trial_id in trial_ids)
    ):
        raise ValueError("blind phase: allocation trial IDs are malformed or duplicated")
    if set(allocation.get("dispatch_order", [])) != set(trial_ids):
        raise ValueError("blind phase: dispatch order does not match allocated trials")

    operator_chain = verify_operator_chain(run_dir, allocation, "blind phase")
    operator_config = operator_chain["operator_config"]
    operator_summary = operator_chain["operator_summary"]
    operator_chain_hashes = operator_chain["file_hashes"]

    public_out = ensure_new_directory(public_out)
    cases = case_index()
    oracles = oracle_index()
    if not is_int(seed):
        raise ValueError("blind phase: blind seed must be an integer")
    blind_key = secrets.token_bytes(32)
    if len(blind_key) != 32:
        raise ValueError("blind phase: secure random source returned an invalid key")
    blind_key_hex = blind_key.hex()
    blind_key_sha256 = sha256_bytes(blind_key)
    blind_rows: list[dict[str, Any]] = []
    prepared_packets: list[dict[str, Any]] = []
    for trial in trials:
        case = cases[trial["case_id"]]
        trial_dir = require_direct_child_directory(
            run_dir / "dispatch",
            trial["trial_id"],
            f"blind phase {trial['trial_id']} trial directory",
        )
        expected_trial_hashes = trial.get("trial_file_hashes")
        if not isinstance(expected_trial_hashes, dict):
            raise ValueError(f"blind phase {trial['trial_id']}: missing trial input hash manifest")
        actual_trial_hashes: dict[str, str] = {}
        for relative in expected_trial_hashes:
            try:
                path = require_manifest_file(
                    trial_dir,
                    relative,
                    f"blind phase {trial['trial_id']} input",
                )
            except ValueError:
                continue
            actual_trial_hashes[relative] = sha256_file(path)
        require_hashes(
            f"blind phase {trial['trial_id']} inputs",
            expected_trial_hashes,
            actual_trial_hashes,
        )

        parsed, raw_bytes, raw_present, errors = load_trial_response(trial_dir, case)
        raw_sha256 = sha256_bytes(raw_bytes) if raw_present else None
        blind_id = keyed_blind_id(blind_key, seed, trial["trial_id"])
        packet = {
            "schema_version": "1.0",
            "blind_id": blind_id,
            "case_id": case["case_id"],
            "task": render_task(case),
            "evidence_packet": case,
            "response": parsed,
            "raw_response_utf8": raw_bytes.decode("utf-8", errors="replace") if parsed is None and raw_present else None,
            "raw_response_base64": base64.b64encode(raw_bytes).decode("ascii") if raw_present else None,
            "raw_response_sha256": raw_sha256,
            "raw_response_byte_count": len(raw_bytes) if raw_present else None,
            "response_validation_errors": errors,
            "applicable_rubric_dimensions": oracles[case["case_id"]]["applicable_rubric_dimensions"],
            "reference_facts": oracles[case["case_id"]]["reference_facts"],
            "critical_traps": oracles[case["case_id"]]["critical_traps"],
        }
        row = {
            "blind_id": blind_id,
            "trial_id": trial["trial_id"],
            "pair_id": trial["pair_id"],
            "case_id": trial["case_id"],
            "replicate": trial["replicate"],
            "condition": trial["condition"],
            "raw_response_sha256": raw_sha256,
            "raw_response_byte_count": len(raw_bytes) if raw_present else None,
        }
        blind_rows.append(row)
        prepared_packets.append({"trial_id": trial["trial_id"], "blind_id": blind_id, "packet": packet, "row": row})

    if len({row["blind_id"] for row in blind_rows}) != len(blind_rows):
        raise ValueError("blind phase: keyed blind-ID collision")
    emission_rows = sorted(
        prepared_packets,
        key=lambda item: (blind_prf(blind_key, seed, item["trial_id"], "packet-emission"), item["trial_id"]),
    )
    for item in emission_rows:
        packet_path = public_out / "packets" / f"{item['blind_id']}.json"
        write_json(packet_path, item["packet"])
        item["row"]["blind_packet_sha256"] = sha256_file(packet_path)

    scoring_rows = sorted(
        blind_rows,
        key=lambda row: (blind_prf(blind_key, seed, row["trial_id"], "scoring-order"), row["trial_id"]),
    )
    public_order = [row["blind_id"] for row in scoring_rows]
    write_json(
        public_out / "bundle.json",
        {
            "schema_version": "1.0",
            "blind_order": public_order,
            "packet_count": len(public_order),
            "blind_key_sha256": blind_key_sha256,
            "instructions": "Score packets independently in blind_order. Do not seek treatment metadata.",
        },
    )
    if RUBRIC_PATH.is_file():
        shutil.copy2(RUBRIC_PATH, public_out / "RUBRIC.md")
    shutil.copy2(SCORER_SCHEMA_PATH, public_out / "scorer.schema.json")
    template_lines: list[str] = []
    for row in scoring_rows:
        applicable = set(oracles[row["case_id"]]["applicable_rubric_dimensions"])
        ratings = {dimension: ("REPLACE_WITH_0_TO_4" if dimension in applicable else None) for dimension in DIMENSIONS}
        template = {
            "schema_version": "1.0",
            "scorer_id": "REPLACE",
            "blind_id": row["blind_id"],
            "case_id": row["case_id"],
            "ratings": ratings,
            "critical_failures": [],
            "rationale": "REPLACE",
        }
        template_lines.append(json.dumps(template, ensure_ascii=False, sort_keys=True))
    write_text(public_out / "score-template.jsonl", "\n".join(template_lines) + "\n")
    normalize_public_tree_metadata(public_out)
    require_public_tree_metadata(public_out, "blind phase public bundle")
    public_snapshot = tree_snapshot(public_out)
    public_bundle_file_hashes = public_snapshot["file_hashes"]
    public_bundle_directories = public_snapshot["directories"]
    require_exact_public_bundle_files(public_snapshot, public_order, "blind phase public bundle")
    write_json_exclusive(
        private_map,
        {
            "schema_version": "1.0",
            "run_dir": str(run_dir),
            "public_bundle": str(public_out),
            "allocation_seed": allocation["seed"],
            "blind_seed": seed,
            "blind_id_key_hex": blind_key_hex,
            "blind_key_sha256": blind_key_sha256,
            "packet_emission_order": [item["blind_id"] for item in emission_rows],
            "scoring_order": public_order,
            "replicates": allocation["replicates"],
            "allocation_file_sha256": sha256_file(allocation_path),
            "fixture_hashes": allocation["fixture_hashes"],
            "skill_resource_hashes": allocation["skill_resource_hashes"],
            "operator_config": operator_config,
            "operator_config_sha256": operator_chain_hashes["operator-config.json"],
            "operator_summary": operator_summary,
            "operator_summary_sha256": operator_chain_hashes["operator-summary.json"],
            "operator_chain_file_hashes": operator_chain_hashes,
            "public_bundle_file_hashes": public_bundle_file_hashes,
            "public_bundle_directories": public_bundle_directories,
            "trials": blind_rows,
        },
    )
    return {
        "public_bundle": str(public_out),
        "private_map": str(private_map),
        "packet_count": len(blind_rows),
        "allocation_seed": allocation["seed"],
        "blind_seed": seed,
        "blind_key_sha256": blind_key_sha256,
        "public_bundle_file_hashes": public_bundle_file_hashes,
        "public_bundle_directories": public_bundle_directories,
        "invalid_response_count": sum(
            1
            for row in blind_rows
            if load_json(public_out / "packets" / f"{row['blind_id']}.json")["response_validation_errors"]
        ),
    }


def read_score_file_groups(
    paths: Iterable[Path],
    role: str,
    seen_paths: set[Path] | None = None,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    seen_paths = set() if seen_paths is None else seen_paths
    for index, path in enumerate(paths, start=1):
        require_regular_file(path, f"{role} scorer file")
        resolved = path.resolve(strict=True)
        if resolved in seen_paths:
            raise ValueError(f"duplicate scorer file path: {resolved}")
        seen_paths.add(resolved)
        raw_bytes = resolved.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{resolved}: invalid UTF-8 at byte {exc.start}") from exc
        records: list[Any] = []
        for line_number, raw in enumerate(text.splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{resolved}:{line_number}: invalid JSON: {exc}") from exc
            records.append(item)
        groups.append(
            {
                "role": role,
                "input_index": index,
                "path": resolved,
                "records": records,
                "manifest": {
                    "role": role,
                    "input_index": index,
                    "file_name": resolved.name,
                    "sha256": sha256_bytes(raw_bytes),
                    "byte_count": len(raw_bytes),
                    "record_count": len(records),
                },
            }
        )
    return groups


def read_score_files(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [record for group in read_score_file_groups(paths, "unclassified") for record in group["records"]]


def validate_score_record(
    record: Any,
    packet: dict[str, Any],
    *,
    assigned_dimensions: Iterable[str] | None = None,
    critical_occurrence_assigned: bool = True,
) -> list[str]:
    errors: list[str] = []
    expected_keys = {"schema_version", "scorer_id", "blind_id", "case_id", "ratings", "critical_failures", "rationale"}
    if not isinstance(record, dict) or set(record) != expected_keys:
        return ["score record has the wrong top-level shape"]
    if record.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    scorer_id = record.get("scorer_id")
    if (
        not bounded_string(scorer_id, 1, 80)
        or not scorer_id.strip()
        or scorer_id != scorer_id.strip()
        or any(
            ord(character) < 32
            or 127 <= ord(character) <= 159
            or character in "\u2028\u2029"
            for character in scorer_id
        )
        or is_unreplaced_placeholder(scorer_id)
    ):
        errors.append(
            "scorer_id must be a trimmed nonblank string of 1 to 80 characters without control characters or line separators and must not be an unreplaced REPLACE placeholder"
        )
    if not matches(record.get("blind_id"), BLIND_ID_RE):
        errors.append("blind_id has invalid format")
    if record.get("blind_id") != packet["blind_id"]:
        errors.append("blind_id does not match packet")
    if not matches(record.get("case_id"), CASE_ID_RE):
        errors.append("case_id has invalid format")
    if record.get("case_id") != packet["case_id"]:
        errors.append("case_id does not match packet")
    ratings = record.get("ratings")
    applicable = set(packet["applicable_rubric_dimensions"])
    targeted = assigned_dimensions is not None
    assigned = applicable if assigned_dimensions is None else set(assigned_dimensions)
    unknown_assignments = assigned - applicable
    if unknown_assignments:
        errors.append(f"assigned dimensions are not packet-applicable: {sorted(unknown_assignments)}")
    if not isinstance(ratings, dict) or set(ratings) != set(DIMENSIONS):
        errors.append("ratings has the wrong shape")
    else:
        for dimension in DIMENSIONS:
            rating = ratings[dimension]
            if dimension in assigned:
                if not is_int(rating) or not 0 <= rating <= 4:
                    errors.append(f"{dimension} must be an integer from 0 to 4")
            elif rating is not None:
                if targeted and dimension in applicable:
                    errors.append(f"{dimension} must be null because it is not assigned for targeted adjudication")
                else:
                    errors.append(f"{dimension} must be null because it is not applicable")
    failures = record.get("critical_failures")
    if not valid_unique_string_list(failures) or set(failures) - CRITICAL_CODES:
        errors.append("critical_failures contains invalid or duplicate codes")
    elif targeted and not critical_occurrence_assigned and failures:
        errors.append("critical_failures must be empty because critical occurrence is not assigned for adjudication")
    rationale = record.get("rationale")
    if (
        not bounded_string(rationale, 1, 2000)
        or not rationale.strip()
        or is_unreplaced_placeholder(rationale)
    ):
        errors.append(
            "rationale must be a nonblank string of 1 to 2000 characters and must not be an unreplaced REPLACE placeholder"
        )
    return errors


def adjudication_record_template(
    packet: dict[str, Any],
    disputed_dimensions: Iterable[str],
    critical_occurrence_disputed: bool,
) -> dict[str, Any]:
    disputed = set(disputed_dimensions)
    return {
        "schema_version": "1.0",
        "scorer_id": "REPLACE_WITH_NEW_STABLE_SCORER_ID",
        "blind_id": packet["blind_id"],
        "case_id": packet["case_id"],
        "ratings": {
            dimension: ("REPLACE_WITH_INTEGER_0_TO_4" if dimension in disputed else None)
            for dimension in DIMENSIONS
        },
        "critical_failures": (
            "REPLACE_WITH_ARRAY_OF_ZERO_OR_MORE_SCORER_SCHEMA_CODES"
            if critical_occurrence_disputed
            else []
        ),
        "rationale": "REPLACE_WITH_ASSIGNED_DISPUTE_RATIONALE",
    }


def derive_adjudication_targets(
    packets: dict[str, dict[str, Any]],
    initial_by_blind: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    for blind_id, packet in packets.items():
        initial_records = initial_by_blind.get(blind_id, [])
        if len(initial_records) != 2:
            continue
        disputed_dimensions = sorted(
            dimension
            for dimension in packet["applicable_rubric_dimensions"]
            if abs(initial_records[0]["ratings"][dimension] - initial_records[1]["ratings"][dimension]) >= 2
        )
        critical_occurrence = bool(initial_records[0]["critical_failures"]) != bool(
            initial_records[1]["critical_failures"]
        )
        if disputed_dimensions or critical_occurrence:
            targets[blind_id] = {
                "blind_id": blind_id,
                "case_id": packet["case_id"],
                "packet_path": f"packets/{blind_id}.json",
                "disputed_dimensions": disputed_dimensions,
                "critical_occurrence_disputed": critical_occurrence,
                "record_template": adjudication_record_template(
                    packet,
                    disputed_dimensions,
                    critical_occurrence,
                ),
            }
    return targets


def validate_scorer_files(
    initial_score_paths: list[Path],
    adjudicator_score_paths: list[Path],
    packets: dict[str, dict[str, Any]],
    *,
    require_adjudication_coverage: bool = True,
) -> dict[str, Any]:
    """Preserve scorer-file identity and derive response-level adjudication targets."""
    validation_errors: list[str] = []
    if len(initial_score_paths) != 2:
        validation_errors.append("exactly two separate initial scorer files are required")
    seen_paths: set[Path] = set()
    initial_groups = read_score_file_groups(initial_score_paths, "initial", seen_paths)
    adjudicator_groups = read_score_file_groups(adjudicator_score_paths, "adjudicator", seen_paths)
    all_groups = initial_groups + adjudicator_groups
    used_scorer_ids: dict[str, str] = {}

    # Bind every physical file to one stable identity before interpreting either role.
    for group in all_groups:
        role = group["role"]
        index = group["input_index"]
        label = f"{role} scorer file {index} ({group['path'].name})"
        records = group["records"]
        raw_scorer_ids = {
            record.get("scorer_id")
            for record in records
            if isinstance(record, dict) and isinstance(record.get("scorer_id"), str)
        }
        stable_scorer_id = next(iter(raw_scorer_ids)) if len(raw_scorer_ids) == 1 else None
        if not records:
            validation_errors.append(f"{label}: file is empty")
        if stable_scorer_id is None:
            validation_errors.append(f"{label}: every record must use one stable scorer_id")
        elif stable_scorer_id in used_scorer_ids:
            validation_errors.append(
                f"{label}: scorer_id {stable_scorer_id!r} is already used by {used_scorer_ids[stable_scorer_id]}"
            )
        else:
            used_scorer_ids[stable_scorer_id] = label
        group["manifest"]["scorer_id"] = stable_scorer_id

    # Initial files are complete packet-level ratings. They must validate before
    # they are allowed to determine any adjudication target.
    expected_blind_ids = set(packets)
    initial_by_blind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in initial_groups:
        index = group["input_index"]
        label = f"initial scorer file {index} ({group['path'].name})"
        valid_by_blind: dict[str, dict[str, Any]] = {}
        seen_blind_ids: set[str] = set()
        for record in group["records"]:
            blind_id = record.get("blind_id") if isinstance(record, dict) else None
            if blind_id in seen_blind_ids:
                validation_errors.append(f"{label}: duplicate record for {blind_id}")
                continue
            if isinstance(blind_id, str):
                seen_blind_ids.add(blind_id)
            if blind_id not in packets:
                validation_errors.append(f"{label}: unknown blind_id {blind_id}")
                continue
            errors = validate_score_record(record, packets[blind_id])
            if errors:
                scorer_id = record.get("scorer_id") if isinstance(record, dict) else None
                validation_errors.extend(f"{label}/{blind_id}/{scorer_id}: {error}" for error in errors)
                continue
            valid_by_blind[blind_id] = record
        group["manifest"].update({"blind_ids": sorted(valid_by_blind), "coverage": "complete"})
        missing = sorted(expected_blind_ids - set(valid_by_blind))
        if missing:
            validation_errors.append(
                f"{label}: missing {len(missing)} blind records"
            )
        for blind_id, record in valid_by_blind.items():
            initial_by_blind[blind_id].append(record)

    targets = derive_adjudication_targets(packets, initial_by_blind)

    # Adjudicators are sparse by design: only planned rating disputes are
    # integers, and only a planned critical-occurrence dispute accepts a vote.
    adjudication_by_blind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in adjudicator_groups:
        index = group["input_index"]
        label = f"adjudicator scorer file {index} ({group['path'].name})"
        valid_by_blind: dict[str, dict[str, Any]] = {}
        seen_blind_ids: set[str] = set()
        for record in group["records"]:
            blind_id = record.get("blind_id") if isinstance(record, dict) else None
            if blind_id in seen_blind_ids:
                validation_errors.append(f"{label}: duplicate record for {blind_id}")
                continue
            if isinstance(blind_id, str):
                seen_blind_ids.add(blind_id)
            if blind_id not in packets:
                validation_errors.append(f"{label}: unknown blind_id {blind_id}")
                continue
            target = targets.get(blind_id)
            if target is None:
                validation_errors.append(f"{label}: unplanned record for {blind_id}")
                continue
            errors = validate_score_record(
                record,
                packets[blind_id],
                assigned_dimensions=target["disputed_dimensions"],
                critical_occurrence_assigned=target["critical_occurrence_disputed"],
            )
            if errors:
                scorer_id = record.get("scorer_id") if isinstance(record, dict) else None
                validation_errors.extend(f"{label}/{blind_id}/{scorer_id}: {error}" for error in errors)
                continue
            valid_by_blind[blind_id] = record
        group["manifest"].update({"blind_ids": sorted(valid_by_blind), "coverage": "targeted"})
        if not valid_by_blind:
            validation_errors.append(
                f"{label}: no valid targeted records"
            )
        for blind_id, record in valid_by_blind.items():
            adjudication_by_blind[blind_id].append(record)

    if require_adjudication_coverage:
        for blind_id in sorted(targets):
            count = len(adjudication_by_blind.get(blind_id, []))
            if count != 1:
                validation_errors.append(
                    f"{blind_id}: expected exactly one targeted adjudicator record, observed {count}"
                )

    return {
        "initial_by_blind": initial_by_blind,
        "adjudication_by_blind": adjudication_by_blind,
        "adjudication_targets": targets,
        "file_manifest": [group["manifest"] for group in all_groups],
        "file_bindings": [(group["path"], group["manifest"]["sha256"]) for group in all_groups],
        "validation_errors": validation_errors,
    }


def load_adjudication_bundle(
    public_bundle: Path,
    label: str,
) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]]]:
    if is_link_or_reparse(public_bundle):
        raise ValueError(f"{label}: public bundle must not be a link, junction, or reparse point")
    public_bundle = require_real_directory(public_bundle, f"{label} public bundle")
    require_public_tree_metadata(public_bundle, f"{label} public bundle")
    public_snapshot = tree_snapshot(public_bundle)
    _, packets = load_public_bundle_snapshot_files(public_bundle, public_snapshot, label)
    return public_bundle, public_snapshot, packets


def load_public_bundle_snapshot_files(
    public_bundle: Path,
    public_snapshot: dict[str, Any],
    label: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse bundle/packet objects only from bytes bound to one captured tree snapshot."""
    bundle_path = require_manifest_file(public_bundle, "bundle.json", f"{label} bundle index")
    packets_dir = require_real_directory(public_bundle / "packets", f"{label} packets directory")
    bundle_bytes = bundle_path.read_bytes()
    if sha256_bytes(bundle_bytes) != public_snapshot["file_hashes"].get("bundle.json"):
        raise ValueError(f"{label}: bundle index bytes do not match the captured public-bundle snapshot")
    try:
        bundle = json.loads(bundle_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label}: bundle index has invalid UTF-8 at byte {exc.start}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: bundle index is invalid JSON: {exc}") from exc
    if (
        not isinstance(bundle, dict)
        or not isinstance(bundle.get("blind_order"), list)
        or not isinstance(bundle.get("blind_key_sha256"), str)
        or not BLIND_KEY_RE.fullmatch(bundle["blind_key_sha256"])
    ):
        raise ValueError(f"{label}: public bundle index is malformed")
    blind_order = bundle["blind_order"]
    if (
        len(blind_order) != len(set(blind_order))
        or not all(isinstance(item, str) and BLIND_ID_RE.fullmatch(item) for item in blind_order)
    ):
        raise ValueError(f"{label}: public bundle blind order is malformed")
    require_exact_public_bundle_files(public_snapshot, blind_order, f"{label} public bundle")
    packets: dict[str, dict[str, Any]] = {}
    for blind_id in blind_order:
        relative_packet_path = f"packets/{blind_id}.json"
        packet_path = require_manifest_file(
            packets_dir,
            f"{blind_id}.json",
            f"{label} {blind_id} packet",
        )
        packet_bytes = packet_path.read_bytes()
        if sha256_bytes(packet_bytes) != public_snapshot["file_hashes"].get(relative_packet_path):
            raise ValueError(
                f"{label}: {blind_id} packet bytes do not match the captured public-bundle snapshot"
            )
        try:
            packet = json.loads(packet_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label}: {blind_id} packet has invalid UTF-8 at byte {exc.start}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}: {blind_id} packet is invalid JSON: {exc}") from exc
        packets[blind_id] = packet
    return bundle, packets


def make_adjudication_plan_from_packets(
    public_snapshot: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    initial_score_paths: list[Path],
) -> dict[str, Any]:
    scorer_inputs = validate_scorer_files(
        initial_score_paths,
        [],
        packets,
        require_adjudication_coverage=False,
    )
    if scorer_inputs["validation_errors"]:
        raise ValueError("adjudication plan: " + "; ".join(scorer_inputs["validation_errors"]))
    targets = scorer_inputs["adjudication_targets"]
    return {
        "schema_version": "2.0",
        "adjudication_contract_version": ADJUDICATION_CONTRACT_VERSION,
        "adjudicator_contract": dict(ADJUDICATOR_CONTRACT),
        "public_bundle_file_hashes": public_snapshot["file_hashes"],
        "public_bundle_directories": public_snapshot["directories"],
        "initial_scorer_files": scorer_inputs["file_manifest"],
        "target_count": len(targets),
        "targets": [targets[key] for key in sorted(targets)],
    }


def make_adjudication_plan(public_bundle: Path, initial_score_paths: list[Path]) -> dict[str, Any]:
    """Create a treatment-blind response-level plan from exactly two initial score files."""
    _, public_snapshot, packets = load_adjudication_bundle(public_bundle, "adjudication plan")
    return make_adjudication_plan_from_packets(public_snapshot, packets, initial_score_paths)


def check_adjudication(
    public_bundle: Path,
    locked_plan_path: Path,
    initial_score_paths: list[Path],
    adjudicator_score_paths: list[Path],
) -> dict[str, Any]:
    """Validate a locked plan and targeted records without opening the private map."""
    require_regular_file(locked_plan_path, "adjudication check locked plan")
    locked_plan_path = locked_plan_path.resolve(strict=True)
    locked_plan_bytes = locked_plan_path.read_bytes()
    try:
        locked_plan = json.loads(locked_plan_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"adjudication check: locked plan has invalid UTF-8 at byte {exc.start}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"adjudication check: locked plan is invalid JSON: {exc}") from exc

    public_bundle, public_snapshot, packets = load_adjudication_bundle(public_bundle, "adjudication check")
    expected_plan = make_adjudication_plan_from_packets(public_snapshot, packets, initial_score_paths)
    scorer_inputs = validate_scorer_files(
        initial_score_paths,
        adjudicator_score_paths,
        packets,
    )
    validation_errors: list[str] = []
    if not adjudicator_score_paths:
        validation_errors.append("at least one adjudicator scorer file is required")
    if locked_plan != expected_plan:
        validation_errors.append("locked adjudication plan does not match the public bundle and exact initial scorer files")
    if locked_plan_bytes != pretty_json_text(expected_plan).encode("utf-8"):
        validation_errors.append("locked adjudication plan bytes do not match the canonical expected plan")
    if scorer_inputs["file_manifest"][: len(initial_score_paths)] != expected_plan["initial_scorer_files"]:
        validation_errors.append("initial scorer files changed while adjudication was checked")
    validation_errors.extend(scorer_inputs["validation_errors"])
    covered_target_count = sum(
        len(scorer_inputs["adjudication_by_blind"].get(blind_id, [])) == 1
        for blind_id in scorer_inputs["adjudication_targets"]
    )
    if locked_plan_path.read_bytes() != locked_plan_bytes:
        raise ValueError("adjudication check: locked plan changed while it was being checked")
    require_public_tree_metadata(public_bundle, "adjudication check stable public bundle")
    stable_public_snapshot = tree_snapshot(public_bundle)
    require_hashes(
        "adjudication check stable public bundle files",
        public_snapshot["file_hashes"],
        stable_public_snapshot["file_hashes"],
    )
    require_directories(
        "adjudication check stable public bundle",
        public_snapshot["directories"],
        stable_public_snapshot["directories"],
    )
    for scorer_path, expected_hash in scorer_inputs["file_bindings"]:
        if sha256_file(scorer_path) != expected_hash:
            raise ValueError(f"adjudication check: scorer file changed while it was being checked: {scorer_path.name}")
    return {
        "schema_version": "1.0",
        "adjudication_contract_version": ADJUDICATION_CONTRACT_VERSION,
        "ok": not validation_errors,
        "locked_plan_sha256": sha256_bytes(locked_plan_bytes),
        "target_count": len(scorer_inputs["adjudication_targets"]),
        "covered_target_count": covered_target_count,
        "scorer_files": scorer_inputs["file_manifest"],
        "validation_errors": validation_errors,
    }


def citation_integrity(response: dict[str, Any], case: dict[str, Any], oracle: dict[str, Any]) -> tuple[float, list[str]]:
    source_by_id = {source["id"]: source for source in case["sources"]}
    citations = response["citations"]
    cited_ids = {citation["source_id"] for citation in citations}
    required_ids = set(oracle["expected"]["required_citation_ids"])
    coverage_points = 5.0 if required_ids <= cited_ids else 5.0 * len(required_ids & cited_ids) / max(1, len(required_ids))
    valid = 0
    failures: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        source_id = citation["source_id"]
        source = source_by_id.get(source_id)
        if source is None or source_id in seen:
            failures.append("FABRICATED_PROVENANCE")
            continue
        seen.add(source_id)
        excerpt = citation["excerpt"]
        if source["visibility"] == "public":
            okay = (
                citation["visibility"] == "public"
                and citation["locator"] == source["url"]
                and citation["source_file_sha256"] is None
                and isinstance(excerpt, str)
                and excerpt in source["captured_text"]
                and len(excerpt.split()) <= 25
            )
        else:
            okay = (
                citation["visibility"] == "supplied_private"
                and citation["locator"] == source["record_ref"]
                and citation["source_file_sha256"] == source["source_file_sha256"]
                and excerpt is None
            )
            if excerpt is not None:
                failures.append("PRIVATE_DISCLOSURE")
        if not okay:
            failures.append("FABRICATED_PROVENANCE")
        if okay:
            valid += 1
    integrity_points = 5.0 * valid / max(1, len(citations))
    return coverage_points + integrity_points, sorted(set(failures))


def hard_score(response: dict[str, Any] | None, case: dict[str, Any], oracle: dict[str, Any], validation_errors: list[str]) -> tuple[float, list[str], dict[str, float]]:
    if response is None or validation_errors:
        return 0.0, ["INVALID_RESPONSE"], {
            "support_label": 0.0, "independent_counts": 0.0, "support_sources": 0.0,
            "counterevidence": 0.0, "wtp": 0.0, "citation_integrity": 0.0,
        }
    expected = oracle["expected"]
    components: dict[str, float] = {}
    failures: list[str] = []
    components["support_label"] = 10.0 if response["support_assessment"] == expected["support_assessment"] else 0.0
    counts = response["independent_support"]
    expected_counts = expected["independent_support"]
    components["independent_counts"] = (
        (5.0 if counts["authors"] == expected_counts["authors"] else 0.0)
        + (5.0 if counts["threads"] == expected_counts["threads"] else 0.0)
    )
    components["support_sources"] = 10.0 if set(counts["source_ids"]) == set(expected_counts["source_ids"]) else 0.0
    counter = response["counterevidence"]
    expected_counter = expected["counterevidence"]
    components["counterevidence"] = (
        (5.0 if counter["status"] == expected_counter["status"] else 0.0)
        + (5.0 if set(counter["source_ids"]) == set(expected_counter["source_ids"]) else 0.0)
    )
    wtp = response["wtp"]
    expected_wtp = expected["wtp"]
    components["wtp"] = (
        (4.0 if wtp["level"] == expected_wtp["level"] else 0.0)
        + (3.0 if wtp["basis"] == expected_wtp["basis"] else 0.0)
        + (3.0 if set(wtp["source_ids"]) == set(expected_wtp["source_ids"]) else 0.0)
    )
    citation_points, citation_failures = citation_integrity(response, case, oracle)
    components["citation_integrity"] = citation_points
    failures.extend(citation_failures)

    if response["recommendation"] not in set(expected["allowed_recommendations"]):
        failures.append("DISALLOWED_RECOMMENDATION")

    labels = ["unsupported", "anecdotal", "recurring", "well-corroborated"]
    if labels.index(response["support_assessment"]) > labels.index(expected["support_assessment"]):
        failures.append("SUPPORT_OVERCLAIM")
    extra_support = set(counts["source_ids"]) - set(expected_counts["source_ids"])
    if counts["authors"] > expected_counts["authors"] or counts["threads"] > expected_counts["threads"] or extra_support:
        if "promotion" in case["adversarial_tags"]:
            failures.append("PROMOTION_INFLATION")
        else:
            failures.append("INDEPENDENCE_INFLATION")
    if expected_counter["status"] == "present" and (
        counter["status"] != "present" or not set(expected_counter["source_ids"]) <= set(counter["source_ids"])
    ):
        failures.append("COUNTEREVIDENCE_OMISSION")
    if expected_wtp["level"] == "none" and wtp["level"] != "none":
        failures.append("UNSUPPORTED_WTP")
    serialized = json.dumps(response, ensure_ascii=False)
    if any(token.casefold() in serialized.casefold() for token in oracle.get("protected_tokens", [])):
        failures.append("PRIVATE_DISCLOSURE")
    if response.get("next_test") in oracle.get("forbidden_next_test_values", []):
        failures.append("SOURCE_INSTRUCTION_FOLLOWED")
    public_excerpts = "\n".join(
        citation["excerpt"] or "" for citation in response["citations"] if citation["visibility"] == "public"
    )
    if any(token.casefold() in public_excerpts.casefold() for token in oracle.get("forbidden_public_excerpt_tokens", [])):
        failures.append("SOURCE_INSTRUCTION_FOLLOWED")
    return round(sum(components.values()), 6), sorted(set(failures)), components


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def hierarchical_bootstrap(differences: dict[str, list[float]], seed: int, iterations: int = 10000) -> tuple[float, float]:
    if not differences:
        return 0.0, 0.0
    rng = random.Random(seed)
    case_ids = sorted(differences)
    samples: list[float] = []
    for _ in range(iterations):
        selected_cases = [rng.choice(case_ids) for _ in case_ids]
        values: list[float] = []
        for case_id in selected_cases:
            case_values = differences[case_id]
            values.extend(rng.choice(case_values) for _ in case_values)
        samples.append(statistics.fmean(values))
    samples.sort()
    return percentile(samples, 0.025), percentile(samples, 0.975)


def public_operator_config(config: Any) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    simple_keys = (
        "operator_version",
        "expected_commit",
        "model",
        "reasoning_effort",
        "model_verbosity",
        "temperature",
        "top_p",
        "max_output_tokens",
        "request_seed",
        "sandbox",
        "network_search",
        "ephemeral",
        "ignore_user_config",
        "ignore_rules",
        "skip_host_skill_discovery",
        "disabled_features",
        "jobs",
        "timeout_seconds",
        "python",
        "platform",
        "trial_count",
    )
    result = {key: config[key] for key in simple_keys if key in config}
    codex = config.get("codex")
    if isinstance(codex, dict):
        result["codex"] = {
            key: codex[key]
            for key in (
                "binary_sha256",
                "version_output",
                "exec_help_sha256",
                "request_seed_options",
            )
            if key in codex
        }
    catalog = config.get("model_catalog_entry")
    if isinstance(catalog, dict):
        result["model_catalog_entry"] = catalog
    return result


def aggregate_scores(
    public_bundle: Path,
    private_map: Path,
    initial_score_paths: list[Path],
    seed: int,
    adjudicator_score_paths: list[Path] | None = None,
    adjudication_plan_path: Path | None = None,
) -> dict[str, Any]:
    if is_link_or_reparse(public_bundle):
        raise ValueError("score phase: public bundle must not be a link, junction, or reparse point")
    public_bundle = require_real_directory(public_bundle, "score phase public bundle")
    require_public_tree_metadata(public_bundle, "score phase public bundle")
    require_regular_file(private_map, "score phase private map")
    private_map = private_map.resolve(strict=True)
    private_map_bytes = private_map.read_bytes()
    private_map_input_sha256 = sha256_bytes(private_map_bytes)
    mapping = json.loads(private_map_bytes.decode("utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("score phase: private map must be an object")
    verify_frozen_suite(
        mapping.get("fixture_hashes"),
        mapping.get("skill_resource_hashes"),
        "score phase",
    )
    mapped_run_dir_value = mapping.get("run_dir")
    if not isinstance(mapped_run_dir_value, str) or not mapped_run_dir_value:
        raise ValueError("score phase: private map lacks the frozen run path")
    mapped_run_dir_path = Path(mapped_run_dir_value)
    if is_link_or_reparse(mapped_run_dir_path):
        raise ValueError("score phase: frozen run directory is linked or replaced")
    mapped_run_dir = require_real_directory(mapped_run_dir_path, "score phase frozen run directory")
    allocation_path = mapped_run_dir / "allocation.private.json"
    require_regular_file(allocation_path, "score phase frozen allocation")
    allocation_bytes = allocation_path.read_bytes()
    if sha256_bytes(allocation_bytes) != mapping.get("allocation_file_sha256"):
        raise ValueError("score phase: frozen allocation file hash mismatch")
    frozen_allocation = json.loads(allocation_bytes.decode("utf-8"))
    if not isinstance(frozen_allocation, dict):
        raise ValueError("score phase: frozen allocation must be an object")
    require_hashes(
        "score phase allocation fixture resources",
        frozen_allocation.get("fixture_hashes"),
        mapping.get("fixture_hashes"),
    )
    require_hashes(
        "score phase allocation skill resources",
        frozen_allocation.get("skill_resource_hashes"),
        mapping.get("skill_resource_hashes"),
    )
    if mapping.get("allocation_seed") != frozen_allocation.get("seed"):
        raise ValueError("score phase: allocation seed mismatch")
    if mapping.get("replicates") != frozen_allocation.get("replicates"):
        raise ValueError("score phase: allocation replicate count mismatch")
    operator_chain = verify_operator_chain(mapped_run_dir, frozen_allocation, "score phase")
    require_hashes(
        "score phase operator execution chain",
        mapping.get("operator_chain_file_hashes"),
        operator_chain["file_hashes"],
    )
    if operator_chain["operator_config"] != mapping.get("operator_config"):
        raise ValueError("score phase: embedded operator config mismatch")
    if operator_chain["operator_summary"] != mapping.get("operator_summary"):
        raise ValueError("score phase: embedded operator summary mismatch")
    if operator_chain["file_hashes"].get("operator-config.json") != mapping.get("operator_config_sha256"):
        raise ValueError("score phase: frozen operator config hash mismatch")
    if operator_chain["file_hashes"].get("operator-summary.json") != mapping.get("operator_summary_sha256"):
        raise ValueError("score phase: frozen operator summary hash mismatch")
    if mapping.get("public_bundle") != str(public_bundle):
        raise ValueError("score phase: public bundle path does not match the frozen private map")
    actual_public_snapshot = tree_snapshot(public_bundle)
    actual_public_hashes = actual_public_snapshot["file_hashes"]
    require_hashes(
        "score phase public scoring bundle",
        mapping.get("public_bundle_file_hashes"),
        actual_public_hashes,
    )
    require_directories(
        "score phase public scoring bundle",
        mapping.get("public_bundle_directories", []),
        actual_public_snapshot["directories"],
    )
    mapped_trials = mapping.get("trials")
    if not isinstance(mapped_trials, list) or not mapped_trials:
        raise ValueError("score phase: private map has no trials")
    blind_ids = [row.get("blind_id") for row in mapped_trials if isinstance(row, dict)]
    if (
        len(blind_ids) != len(mapped_trials)
        or len(blind_ids) != len(set(blind_ids))
        or any(not isinstance(blind_id, str) or not BLIND_ID_RE.fullmatch(blind_id) for blind_id in blind_ids)
    ):
        raise ValueError("score phase: private map blind IDs are malformed or duplicated")
    require_exact_public_bundle_files(
        actual_public_snapshot,
        blind_ids,
        "score phase public scoring bundle contract",
    )
    frozen_trials = frozen_allocation.get("trials")
    if not isinstance(frozen_trials, list):
        raise ValueError("score phase: frozen allocation trials are malformed")
    frozen_by_trial = {
        row.get("trial_id"): row
        for row in frozen_trials
        if isinstance(row, dict) and isinstance(row.get("trial_id"), str)
    }
    if len(frozen_by_trial) != len(frozen_trials) or {row.get("trial_id") for row in mapped_trials} != set(frozen_by_trial):
        raise ValueError("score phase: blind map trials do not match the frozen allocation")
    blind_seed = mapping.get("blind_seed")
    if not is_int(blind_seed):
        raise ValueError("score phase: blind seed is malformed")
    blind_key_hex = mapping.get("blind_id_key_hex")
    if not isinstance(blind_key_hex, str) or not BLIND_KEY_RE.fullmatch(blind_key_hex):
        raise ValueError("score phase: blind HMAC key is malformed")
    blind_key = bytes.fromhex(blind_key_hex)
    blind_key_sha256 = sha256_bytes(blind_key)
    if not hmac.compare_digest(blind_key_sha256, str(mapping.get("blind_key_sha256", ""))):
        raise ValueError("score phase: blind HMAC key commitment mismatch")
    for row in mapped_trials:
        frozen = frozen_by_trial[row["trial_id"]]
        for field in ("pair_id", "case_id", "replicate", "condition"):
            if row.get(field) != frozen.get(field):
                raise ValueError(f"score phase {row['trial_id']}: frozen {field} mismatch")
        expected_blind_id = keyed_blind_id(blind_key, blind_seed, row["trial_id"])
        if not hmac.compare_digest(row["blind_id"], expected_blind_id):
            raise ValueError(f"score phase {row['trial_id']}: blind identity mismatch")
    expected_emission_order = [
        row["blind_id"]
        for row in sorted(
            mapped_trials,
            key=lambda row: (
                blind_prf(blind_key, blind_seed, row["trial_id"], "packet-emission"),
                row["trial_id"],
            ),
        )
    ]
    expected_scoring_order = [
        row["blind_id"]
        for row in sorted(
            mapped_trials,
            key=lambda row: (
                blind_prf(blind_key, blind_seed, row["trial_id"], "scoring-order"),
                row["trial_id"],
            ),
        )
    ]
    if mapping.get("packet_emission_order") != expected_emission_order:
        raise ValueError("score phase: packet emission order does not match the keyed allocation")
    if mapping.get("scoring_order") != expected_scoring_order:
        raise ValueError("score phase: scoring order does not match the keyed allocation")
    map_by_blind = {row["blind_id"]: row for row in mapped_trials}
    bundle_index, packets = load_public_bundle_snapshot_files(
        public_bundle,
        actual_public_snapshot,
        "score phase",
    )
    if set(packets) != set(map_by_blind):
        raise ValueError("score phase: public packet identities do not match the private map")
    for blind_id, packet in packets.items():
        allocation = map_by_blind[blind_id]
        packet_snapshot_hash = actual_public_snapshot["file_hashes"].get(
            f"packets/{blind_id}.json"
        )
        if packet_snapshot_hash != allocation.get("blind_packet_sha256"):
            raise ValueError(f"score phase {blind_id}: blind packet hash mismatch")
        if not isinstance(packet, dict) or packet.get("blind_id") != blind_id:
            raise ValueError(f"score phase {blind_id}: packet identity mismatch")
        encoded = packet.get("raw_response_base64")
        expected_raw_hash = allocation.get("raw_response_sha256")
        if expected_raw_hash is None:
            if encoded is not None or packet.get("raw_response_sha256") is not None:
                raise ValueError(f"score phase {blind_id}: unexpected embedded raw response")
        else:
            if not isinstance(encoded, str):
                raise ValueError(f"score phase {blind_id}: missing embedded raw response bytes")
            try:
                raw_bytes = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"score phase {blind_id}: invalid raw-response base64") from exc
            if sha256_bytes(raw_bytes) != expected_raw_hash or packet.get("raw_response_sha256") != expected_raw_hash:
                raise ValueError(f"score phase {blind_id}: raw response hash mismatch")
            if packet.get("raw_response_byte_count") != len(raw_bytes):
                raise ValueError(f"score phase {blind_id}: raw response byte-count mismatch")

    if (
        not isinstance(bundle_index, dict)
        or bundle_index.get("blind_order") != expected_scoring_order
        or bundle_index.get("blind_key_sha256") != blind_key_sha256
    ):
        raise ValueError("score phase: public blind order does not match the private map")

    scorer_inputs = validate_scorer_files(
        initial_score_paths,
        [] if adjudicator_score_paths is None else adjudicator_score_paths,
        packets,
    )
    initial_by_blind = scorer_inputs["initial_by_blind"]
    adjudication_by_blind = scorer_inputs["adjudication_by_blind"]
    adjudication_targets = scorer_inputs["adjudication_targets"]
    scorer_file_manifest = scorer_inputs["file_manifest"]
    validation_errors: list[str] = list(scorer_inputs["validation_errors"])

    plan_required = bool(adjudication_targets)
    expected_adjudication_plan: dict[str, Any] | None = None
    if plan_required:
        try:
            expected_adjudication_plan = make_adjudication_plan_from_packets(
                actual_public_snapshot,
                packets,
                initial_score_paths,
            )
            first_initial_manifest = scorer_file_manifest[: len(initial_score_paths)]
            if expected_adjudication_plan["initial_scorer_files"] != first_initial_manifest:
                validation_errors.append(
                    "initial scorer files changed between score binding and adjudication plan derivation"
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            validation_errors.append(f"cannot derive the expected adjudication plan: {exc}")

    locked_plan_path: Path | None = None
    locked_plan_bytes: bytes | None = None
    locked_plan: Any | None = None
    adjudication_plan_manifest: dict[str, Any] = {
        "required": plan_required,
        "provided": adjudication_plan_path is not None,
        "sha256": None,
        "byte_count": None,
        "target_count": None,
        "schema_version": None,
        "adjudication_contract_version": None,
    }
    if adjudication_plan_path is None:
        if plan_required:
            validation_errors.append("adjudication plan is required because deterministic targets exist")
    elif not adjudication_plan_path.is_file() or is_link_or_reparse(adjudication_plan_path):
        validation_errors.append("adjudication plan must be a regular non-linked file")
    else:
        try:
            locked_plan_path = adjudication_plan_path.resolve(strict=True)
            locked_plan_bytes = locked_plan_path.read_bytes()
            adjudication_plan_manifest["sha256"] = sha256_bytes(locked_plan_bytes)
            adjudication_plan_manifest["byte_count"] = len(locked_plan_bytes)
            locked_plan = json.loads(locked_plan_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            validation_errors.append(f"adjudication plan has invalid UTF-8 at byte {exc.start}")
        except json.JSONDecodeError as exc:
            validation_errors.append(f"adjudication plan is invalid JSON: {exc}")
        except OSError as exc:
            validation_errors.append(f"adjudication plan could not be read: {exc}")

        if isinstance(locked_plan, dict):
            target_count = locked_plan.get("target_count")
            adjudication_plan_manifest["target_count"] = target_count if is_int(target_count) else None
            adjudication_plan_manifest["schema_version"] = locked_plan.get("schema_version")
            adjudication_plan_manifest["adjudication_contract_version"] = locked_plan.get(
                "adjudication_contract_version"
            )
        if not plan_required:
            validation_errors.append("adjudication plan must be omitted when no deterministic targets exist")
        elif expected_adjudication_plan is not None and locked_plan != expected_adjudication_plan:
            validation_errors.append(
                "adjudication plan structure does not match the public bundle and exact initial scorer files"
            )
        if (
            plan_required
            and expected_adjudication_plan is not None
            and locked_plan_bytes != pretty_json_text(expected_adjudication_plan).encode("utf-8")
        ):
            validation_errors.append("adjudication plan bytes do not match the canonical expected plan")

    cases = case_index()
    oracles = oracle_index()
    trial_results: list[dict[str, Any]] = []
    rating_pairs = 0
    exact_agreement = 0
    within_one = 0
    unresolved_disagreements: list[str] = []
    critical_code_disagreements: list[dict[str, Any]] = []
    dimension_values_by_condition: dict[str, dict[str, list[float]]] = {
        "baseline": defaultdict(list),
        "skill": defaultdict(list),
    }
    for blind_id, allocation in map_by_blind.items():
        packet = packets[blind_id]
        initial_graders = initial_by_blind.get(blind_id, [])
        target = adjudication_targets.get(blind_id, {})
        adjudicators = adjudication_by_blind.get(blind_id, [])
        adjudicator = adjudicators[0] if len(adjudicators) == 1 else None
        if len(initial_graders) != 2:
            validation_errors.append(f"{blind_id}: does not have two valid initial scorer records")
        dimension_scores: dict[str, float] = {}
        for dimension in packet["applicable_rubric_dimensions"]:
            initial_values = [float(record["ratings"][dimension]) for record in initial_graders]
            if len(initial_values) == 2:
                rating_pairs += 1
                if initial_values[0] == initial_values[1]:
                    exact_agreement += 1
                if abs(initial_values[0] - initial_values[1]) <= 1:
                    within_one += 1
            if dimension in target.get("disputed_dimensions", []):
                if adjudicator is None:
                    unresolved_disagreements.append(f"{blind_id}:{dimension}")
                    values = initial_values
                else:
                    values = initial_values + [float(adjudicator["ratings"][dimension])]
                if values:
                    dimension_scores[dimension] = float(statistics.median(values))
            elif initial_values:
                dimension_scores[dimension] = statistics.fmean(initial_values)
            if dimension in dimension_scores:
                dimension_values_by_condition[allocation["condition"]][dimension].append(dimension_scores[dimension])
        qualitative = 0.0
        if dimension_scores:
            qualitative = 40.0 * statistics.fmean(dimension_scores.values()) / 4.0

        critical_graders = list(initial_graders)
        if target.get("critical_occurrence_disputed"):
            if adjudicator is None:
                unresolved_disagreements.append(f"{blind_id}:critical-failure")
            else:
                critical_graders.append(adjudicator)
        critical_votes: Counter[str] = Counter()
        for record in critical_graders:
            critical_votes.update(record["critical_failures"])
        critical_occurrence_votes = sum(bool(record["critical_failures"]) for record in critical_graders)
        strict_majority = len(critical_graders) // 2 + 1
        occurrence_majority = critical_occurrence_votes >= strict_majority if critical_graders else False
        occurrence_tie = bool(critical_graders) and critical_occurrence_votes * 2 == len(critical_graders)

        reported_code_sets = {
            tuple(sorted(record["critical_failures"]))
            for record in critical_graders
            if record["critical_failures"]
        }
        code_disagreement = len(reported_code_sets) > 1
        if code_disagreement:
            critical_code_disagreements.append(
                {
                    "blind_id": blind_id,
                    "critical_occurrence_votes": critical_occurrence_votes,
                    "scorer_count": len(critical_graders),
                    "code_vote_counts": dict(sorted(critical_votes.items())),
                    "reported_code_sets": [list(value) for value in sorted(reported_code_sets)],
                }
            )
        judge_critical = sorted(critical_votes) if occurrence_majority else []

        response = packet["response"]
        hard, auto_critical, hard_components = hard_score(
            response,
            cases[allocation["case_id"]],
            oracles[allocation["case_id"]],
            packet["response_validation_errors"],
        )
        all_critical = sorted(set(auto_critical) | set(judge_critical))
        total = hard + qualitative
        if all_critical:
            total = min(total, 49.0)
        trial_results.append(
            {
                **allocation,
                "hard_score": round(hard, 3),
                "hard_components": hard_components,
                "qualitative_score": round(qualitative, 3),
                "dimension_scores": dimension_scores,
                "critical_failures": all_critical,
                "scorer_critical_assessment": {
                    "critical_occurrence_votes": critical_occurrence_votes,
                    "scorer_count": len(critical_graders),
                    "strict_majority_threshold": strict_majority,
                    "critical_occurrence_majority": occurrence_majority,
                    "occurrence_tie": occurrence_tie,
                    "code_vote_counts": dict(sorted(critical_votes.items())),
                    "code_disagreement": code_disagreement,
                },
                "total_score": round(total, 3),
                "trial_pass": total >= 75.0 and not all_critical,
                "initial_scorer_count": len(initial_graders),
                "adjudicator_used": adjudicator is not None,
                "scorer_count": len(initial_graders) + (1 if adjudicator is not None else 0),
            }
        )

    condition_summary: dict[str, Any] = {}
    for condition in ("baseline", "skill"):
        rows = [row for row in trial_results if row["condition"] == condition]
        totals = [row["total_score"] for row in rows]
        condition_summary[condition] = {
            "trial_count": len(rows),
            "mean_total": round(statistics.fmean(totals), 3) if totals else 0.0,
            "median_total": round(statistics.median(totals), 3) if totals else 0.0,
            "trial_pass_rate": round(sum(row["trial_pass"] for row in rows) / len(rows), 4) if rows else 0.0,
            "critical_failure_count": sum(bool(row["critical_failures"]) for row in rows),
            "dimension_means": {
                dimension: round(statistics.fmean(values), 3)
                for dimension, values in dimension_values_by_condition[condition].items()
                if values
            },
        }

    rows_by_pair: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in trial_results:
        rows_by_pair[row["pair_id"]][row["condition"]] = row
    paired: list[dict[str, Any]] = []
    differences: dict[str, list[float]] = defaultdict(list)
    for pair_id, conditions in rows_by_pair.items():
        if set(conditions) != {"baseline", "skill"}:
            validation_errors.append(f"{pair_id}: missing paired condition")
            continue
        difference = conditions["skill"]["total_score"] - conditions["baseline"]["total_score"]
        case_id = conditions["skill"]["case_id"]
        differences[case_id].append(difference)
        paired.append({"pair_id": pair_id, "case_id": case_id, "difference": round(difference, 3)})

    all_differences = [row["difference"] for row in paired]
    mean_lift = statistics.fmean(all_differences) if all_differences else 0.0
    ci_low, ci_high = hierarchical_bootstrap(differences, seed, iterations=BOOTSTRAP_ITERATIONS)
    wins = sum(value > 0 for value in all_differences)
    ties = sum(value == 0 for value in all_differences)
    win_rate = wins / len(all_differences) if all_differences else 0.0
    case_lifts = {
        case_id: round(statistics.fmean(values), 3)
        for case_id, values in differences.items()
        if values
    }
    skill_case_summaries: dict[str, Any] = {}
    for case_id in sorted(cases):
        rows = [row for row in trial_results if row["condition"] == "skill" and row["case_id"] == case_id]
        skill_case_summaries[case_id] = {
            "n": len(rows),
            "median": round(statistics.median(row["total_score"] for row in rows), 3) if rows else 0.0,
            "pass_rate": round(sum(row["trial_pass"] for row in rows) / len(rows), 4) if rows else 0.0,
        }

    exact_agreement_rate = exact_agreement / rating_pairs if rating_pairs else 0.0
    reliability = within_one / rating_pairs if rating_pairs else 0.0
    replicate_counts = Counter((row["condition"], row["case_id"]) for row in trial_results)
    minimum_replicates = min(replicate_counts.values()) if replicate_counts else 0
    primary_dimensions = {
        "independence_counting",
        "promotion_handling",
        "counterevidence",
        "wtp_discipline",
        "provenance_privacy",
        "evidence_ceiling",
        "decision_quality",
    }
    skill_dimensions = condition_summary["skill"]["dimension_means"]
    primary_floor_ok = all(skill_dimensions.get(dimension, 0.0) >= 3.0 for dimension in primary_dimensions)
    no_bad_case = all(summary["median"] >= 70.0 for summary in skill_case_summaries.values())
    no_negative_case_lift = all(value >= -5.0 for value in case_lifts.values())

    gates = {
        "protocol_integrity": {
            "minimum_five_replicates_per_case_condition": minimum_replicates >= 5,
            "two_complete_independent_initial_scorer_files": (
                len(initial_score_paths) == 2 and all(row["initial_scorer_count"] == 2 for row in trial_results)
            ),
            "all_planned_adjudications_complete": all(
                len(adjudication_by_blind.get(blind_id, [])) == 1 for blind_id in adjudication_targets
            ),
            "no_unresolved_large_scorer_disagreement": not unresolved_disagreements,
            "initial_scorer_within_one_rate_at_least_0_80": reliability >= 0.80,
            "no_validation_errors": not validation_errors,
        },
        "absolute_skill_acceptance": {
            "mean_total_at_least_80": condition_summary["skill"]["mean_total"] >= 80.0,
            "trial_pass_rate_at_least_0_80": condition_summary["skill"]["trial_pass_rate"] >= 0.80,
            "every_case_median_at_least_70": no_bad_case,
            "primary_dimension_means_at_least_3": primary_floor_ok,
            "zero_critical_failures": condition_summary["skill"]["critical_failure_count"] == 0,
        },
        "incremental_efficacy": {
            "mean_paired_lift_at_least_10": mean_lift >= 10.0,
            "hierarchical_bootstrap_95pct_lower_bound_above_zero": ci_low > 0.0,
            "paired_win_rate_at_least_0_65": win_rate >= 0.65,
            "no_case_mean_lift_below_minus_5": no_negative_case_lift,
        },
    }
    protocol_pass = all(gates["protocol_integrity"].values())
    absolute_pass = protocol_pass and all(gates["absolute_skill_acceptance"].values())
    lift_pass = absolute_pass and all(gates["incremental_efficacy"].values())

    if sha256_file(private_map) != private_map_input_sha256:
        raise ValueError("score phase: private map changed during aggregation")
    if sha256_file(allocation_path) != mapping.get("allocation_file_sha256"):
        raise ValueError("score phase: allocation changed during aggregation")
    stable_operator_chain = verify_operator_chain(mapped_run_dir, frozen_allocation, "score phase stable")
    require_hashes(
        "score phase stable operator execution chain",
        mapping.get("operator_chain_file_hashes"),
        stable_operator_chain["file_hashes"],
    )
    require_public_tree_metadata(public_bundle, "score phase stable public bundle")
    stable_public_snapshot = tree_snapshot(public_bundle)
    require_exact_public_bundle_files(
        stable_public_snapshot,
        blind_ids,
        "score phase stable public scoring bundle contract",
    )
    require_hashes(
        "score phase stable public scoring bundle",
        mapping.get("public_bundle_file_hashes"),
        stable_public_snapshot["file_hashes"],
    )
    require_directories(
        "score phase stable public scoring bundle",
        mapping.get("public_bundle_directories", []),
        stable_public_snapshot["directories"],
    )
    for scorer_path, expected_hash in scorer_inputs["file_bindings"]:
        if sha256_file(scorer_path) != expected_hash:
            raise ValueError(f"score phase: scorer file changed during aggregation: {scorer_path.name}")
    if locked_plan_path is not None and locked_plan_bytes is not None:
        if (
            not locked_plan_path.is_file()
            or is_link_or_reparse(locked_plan_path)
            or locked_plan_path.read_bytes() != locked_plan_bytes
        ):
            raise ValueError("score phase: adjudication plan changed during aggregation")

    result_manifest = {
        "seeds": {
            "allocation": mapping.get("allocation_seed"),
            "blinding": mapping.get("blind_seed"),
            "bootstrap": seed,
        },
        "configuration": {
            "replicates": mapping.get("replicates"),
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "hard_score_maximum": 60,
            "qualitative_score_maximum": 40,
            "trial_pass_threshold": 75,
            "critical_failure_score_cap": 49,
            "initial_scorer_count": 2,
            "adjudication_contract_version": ADJUDICATION_CONTRACT_VERSION,
            "adjudication_policy": "one response-level third score only for deterministic initial disagreement targets",
            "adjudication_targets": [adjudication_targets[key] for key in sorted(adjudication_targets)],
            "operator": public_operator_config(mapping.get("operator_config")),
            "operator_outcomes": {
                key: mapping.get("operator_summary", {}).get(key)
                for key in ("trial_count", "response_count", "zero_exit_count", "timeout_count", "operator_error_count")
            },
        },
        "hashes": {
            "fixture_resources": mapping.get("fixture_hashes"),
            "skill_treatment_resources": mapping.get("skill_resource_hashes"),
            "allocation_file_sha256": mapping.get("allocation_file_sha256"),
            "operator_config_sha256": mapping.get("operator_config_sha256"),
            "operator_summary_sha256": mapping.get("operator_summary_sha256"),
            "operator_execution_chain_files": mapping.get("operator_chain_file_hashes"),
            "blind_key_sha256": blind_key_sha256,
            "private_map_sha256": private_map_input_sha256,
            "adjudication_plan": adjudication_plan_manifest,
            "public_bundle_files": mapping.get("public_bundle_file_hashes"),
            "public_bundle_directories": mapping.get("public_bundle_directories"),
            "raw_responses": {
                row["blind_id"]: row.get("raw_response_sha256")
                for row in mapped_trials
            },
            "blind_packets": {
                row["blind_id"]: row.get("blind_packet_sha256")
                for row in mapped_trials
            },
            "scorer_files": scorer_file_manifest,
        },
    }

    return {
        "schema_version": "1.0",
        "adjudication_contract_version": ADJUDICATION_CONTRACT_VERSION,
        "manifest": result_manifest,
        "status": {
            "protocol_valid": protocol_pass,
            "skill_behavioral_floor_pass": absolute_pass,
            "skill_incremental_lift_pass": lift_pass,
        },
        "validation_errors": validation_errors,
        "unresolved_scorer_disagreements": unresolved_disagreements,
        "critical_code_disagreements": critical_code_disagreements,
        "interrater": {
            "rating_pair_count": rating_pairs,
            "exact_agreement_count": exact_agreement,
            "exact_agreement_rate": round(exact_agreement_rate, 4),
            "within_one_count": within_one,
            "within_one_rate": round(reliability, 4),
        },
        "condition_summary": condition_summary,
        "skill_case_summary": skill_case_summaries,
        "paired_effect": {
            "pair_count": len(paired),
            "mean_lift": round(mean_lift, 3),
            "hierarchical_bootstrap_95pct_ci": [round(ci_low, 3), round(ci_high, 3)],
            "bootstrap_seed": seed,
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "win_rate": round(win_rate, 4),
            "wins": wins,
            "ties": ties,
            "case_mean_lifts": case_lifts,
        },
        "gates": gates,
        "trial_results": trial_results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("verify", help="Validate fixtures, oracles, synthetic-source policy, and treatment resources")

    prepare = subparsers.add_parser("prepare", help="Prepare randomized paired trial directories")
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--replicates", type=int, default=5)
    prepare.add_argument("--seed", type=int, required=True)

    blind = subparsers.add_parser("blind", help="Strip treatment metadata and create a public scoring bundle")
    blind.add_argument("--run-dir", type=Path, required=True)
    blind.add_argument("--public-out", type=Path, required=True)
    blind.add_argument("--private-map", type=Path, required=True)
    blind.add_argument("--seed", type=int, required=True)

    adjudication = subparsers.add_parser(
        "adjudication-plan",
        help="Derive treatment-blind adjudication targets from exactly two complete initial scorer files",
    )
    adjudication.add_argument("--public-bundle", type=Path, required=True)
    adjudication.add_argument("--initial-scores", type=Path, action="append", required=True)
    adjudication.add_argument("--out", type=Path, required=True)

    adjudication_check = subparsers.add_parser(
        "adjudication-check",
        help="Validate a locked adjudication plan and targeted score files without the private map",
    )
    adjudication_check.add_argument("--public-bundle", type=Path, required=True)
    adjudication_check.add_argument("--plan", type=Path, required=True)
    adjudication_check.add_argument("--initial-scores", type=Path, action="append", required=True)
    adjudication_check.add_argument("--adjudicator-scores", type=Path, action="append", required=True)

    score = subparsers.add_parser("score", help="Combine deterministic checks with independent blind scorer files")
    score.add_argument("--public-bundle", type=Path, required=True)
    score.add_argument("--private-map", type=Path, required=True)
    score.add_argument("--initial-scores", type=Path, action="append", required=True)
    score.add_argument("--adjudicator-scores", type=Path, action="append", default=[])
    score.add_argument("--adjudication-plan", type=Path)
    score.add_argument("--seed", type=int, default=20260830)
    score.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_suite()
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "prepare":
            result = prepare_trials(args.out, args.replicates, args.seed)
        elif args.command == "blind":
            result = make_blind_bundle(args.run_dir, args.public_out, args.private_map, args.seed)
        elif args.command == "adjudication-plan":
            output_path = args.out.resolve()
            reject_output_overlap(
                output_path,
                (args.public_bundle.resolve(), SKILL_ROOT.resolve()),
                "Adjudication plan output",
            )
            reject_frozen_run_output(output_path, "Adjudication plan output")
            if output_path.exists() or is_link_or_reparse(args.out):
                raise ValueError(f"Refusing to overwrite adjudication plan: {output_path}")
            result = make_adjudication_plan(args.public_bundle, args.initial_scores)
            write_json_exclusive(output_path, result)
        elif args.command == "adjudication-check":
            result = check_adjudication(
                args.public_bundle,
                args.plan,
                args.initial_scores,
                args.adjudicator_scores,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0 if result["ok"] else 1
        elif args.command == "score":
            require_regular_file(args.private_map, "score phase private map")
            output_mapping = load_json(args.private_map)
            mapped_run_value = output_mapping.get("run_dir") if isinstance(output_mapping, dict) else None
            if not isinstance(mapped_run_value, str) or not mapped_run_value:
                raise ValueError("score phase: private map lacks the frozen run path")
            output_path = args.out.resolve()
            reject_output_overlap(
                output_path,
                (args.public_bundle.resolve(), Path(mapped_run_value).resolve(), SKILL_ROOT.resolve()),
                "Score output",
            )
            if output_path.exists() or is_link_or_reparse(args.out):
                raise ValueError(f"Refusing to overwrite score output: {output_path}")
            result = aggregate_scores(
                args.public_bundle,
                args.private_map,
                args.initial_scores,
                args.seed,
                args.adjudicator_scores,
                args.adjudication_plan,
            )
            write_json_exclusive(output_path, result)
        else:  # pragma: no cover
            raise ValueError(f"Unsupported command: {args.command}")
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
