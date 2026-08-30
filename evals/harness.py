#!/usr/bin/env python3
"""Dependency-free harness for the community-signal behavioral A/B evaluation.

The harness never calls a model. It verifies fixtures, prepares isolated trial
bundles, removes treatment labels before scoring, performs deterministic hard
checks, aggregates blind rubric ratings, and evaluates preregistered gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
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


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def opaque_id(*parts: Any, length: int = 16) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return sha256_bytes(raw)[:length]


def case_index() -> dict[str, dict[str, Any]]:
    payload = load_json(CASES_PATH)
    return {case["case_id"]: case for case in payload["cases"]}


def oracle_index() -> dict[str, dict[str, Any]]:
    return load_json(ORACLES_PATH)["cases"]


def ensure_new_directory(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


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
    out_dir = out_dir.resolve()
    ensure_new_directory(out_dir)

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

`allocation.private.json` is treatment-bearing and must never go to agents or scorers. Run trials in `dispatch_order`, each in a fresh isolated agent session. Give the agent only that trial directory. Save the unedited final output as `response.raw.txt` in the same directory; if the runtime can emit exact JSON, `response.json` is also accepted. Do not retry or repair a malformed answer. See the committed evaluation protocol for isolation and configuration requirements.
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
    stripped = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped), []
    except json.JSONDecodeError as exc:
        return None, [f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"]


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
    if value.get("case_id") != case["case_id"]:
        errors.append("case_id does not match packet")
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
        if not valid_unique_string_list(support.get("source_ids")):
            errors.append("independent_support.source_ids must be a unique string array")

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
            if not isinstance(item.get("explanation"), str) or not item["explanation"].strip():
                errors.append(f"excluded_or_collapsed_sources[{index}] lacks explanation")

    counter = value.get("counterevidence")
    if not isinstance(counter, dict) or set(counter) != {"status", "source_ids", "summary"}:
        errors.append("counterevidence has the wrong shape")
    else:
        if counter.get("status") not in COUNTER_STATUSES:
            errors.append("invalid counterevidence.status")
        if not valid_unique_string_list(counter.get("source_ids")):
            errors.append("counterevidence.source_ids must be a unique string array")
        if not isinstance(counter.get("summary"), str) or not counter["summary"].strip():
            errors.append("counterevidence.summary must be non-empty")

    wtp = value.get("wtp")
    if not isinstance(wtp, dict) or set(wtp) != {"level", "basis", "source_ids", "summary"}:
        errors.append("wtp has the wrong shape")
    else:
        if wtp.get("level") not in WTP_LEVELS:
            errors.append("invalid wtp.level")
        if wtp.get("basis") not in WTP_BASES:
            errors.append("invalid wtp.basis")
        if not valid_unique_string_list(wtp.get("source_ids")):
            errors.append("wtp.source_ids must be a unique string array")
        if not isinstance(wtp.get("summary"), str) or not wtp["summary"].strip():
            errors.append("wtp.summary must be non-empty")

    if not isinstance(value.get("public_memo"), str) or not value["public_memo"].strip():
        errors.append("public_memo must be non-empty")
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
            if not isinstance(citation.get("locator"), str) or not citation["locator"]:
                errors.append(f"citations[{index}] has invalid locator")
            excerpt = citation.get("excerpt")
            if excerpt is not None and (not isinstance(excerpt, str) or not excerpt.strip()):
                errors.append(f"citations[{index}] has invalid excerpt")
            source_hash = citation.get("source_file_sha256")
            if source_hash is not None and (
                not isinstance(source_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash)
            ):
                errors.append(f"citations[{index}] has invalid source_file_sha256")
            if citation.get("visibility") == "public":
                if source_hash is not None:
                    errors.append(f"citations[{index}] public citation must not carry source_file_sha256")
                if not isinstance(excerpt, str) or not excerpt.strip():
                    errors.append(f"citations[{index}] public citation must include excerpt")
            elif citation.get("visibility") == "supplied_private":
                if not isinstance(source_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash):
                    errors.append(f"citations[{index}] private citation must include source_file_sha256")
                if excerpt is not None:
                    errors.append(f"citations[{index}] private citation must not include excerpt")
    limitations = value.get("limitations")
    if not valid_unique_string_list(limitations) or not limitations:
        errors.append("limitations must be a non-empty unique string array")
    if not isinstance(value.get("next_test"), str) or not value["next_test"].strip():
        errors.append("next_test must be non-empty")
    return errors


def valid_unique_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and len(value) == len(set(value))


def load_trial_response(trial_dir: Path, case: dict[str, Any]) -> tuple[Any | None, str, list[str]]:
    json_path = trial_dir / "response.json"
    raw_path = trial_dir / "response.raw.txt"
    if json_path.is_file():
        raw = json_path.read_text(encoding="utf-8")
    elif raw_path.is_file():
        raw = raw_path.read_text(encoding="utf-8")
    else:
        return None, "", ["missing response.json or response.raw.txt"]
    parsed, parse_errors = parse_model_output(raw)
    if parse_errors:
        return None, raw, parse_errors
    return parsed, raw, validate_response(parsed, case)


def make_blind_bundle(run_dir: Path, public_out: Path, private_map: Path, seed: int) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    public_out = public_out.resolve()
    private_map = private_map.resolve()
    if public_out == private_map or public_out in private_map.parents:
        raise ValueError("private map must be outside the public scoring bundle")
    ensure_new_directory(public_out)
    if private_map.exists():
        raise ValueError(f"Refusing to overwrite private map: {private_map}")

    allocation = load_json(run_dir / "allocation.private.json")
    cases = case_index()
    oracles = oracle_index()
    rng = random.Random(seed)
    blind_rows: list[dict[str, Any]] = []
    for trial in allocation["trials"]:
        case = cases[trial["case_id"]]
        trial_dir = run_dir / "dispatch" / trial["trial_id"]
        parsed, raw, errors = load_trial_response(trial_dir, case)
        blind_id = "blind-" + opaque_id(seed, trial["trial_id"], "scoring", length=16)
        packet = {
            "schema_version": "1.0",
            "blind_id": blind_id,
            "case_id": case["case_id"],
            "task": render_task(case),
            "evidence_packet": case,
            "response": parsed,
            "raw_response": raw if parsed is None else None,
            "response_validation_errors": errors,
            "applicable_rubric_dimensions": oracles[case["case_id"]]["applicable_rubric_dimensions"],
            "reference_facts": oracles[case["case_id"]]["reference_facts"],
            "critical_traps": oracles[case["case_id"]]["critical_traps"],
        }
        write_json(public_out / "packets" / f"{blind_id}.json", packet)
        blind_rows.append(
            {
                "blind_id": blind_id,
                "trial_id": trial["trial_id"],
                "pair_id": trial["pair_id"],
                "case_id": trial["case_id"],
                "replicate": trial["replicate"],
                "condition": trial["condition"],
            }
        )

    rng.shuffle(blind_rows)
    public_order = [row["blind_id"] for row in blind_rows]
    write_json(
        public_out / "bundle.json",
        {
            "schema_version": "1.0",
            "blind_order": public_order,
            "packet_count": len(public_order),
            "instructions": "Score packets independently in blind_order. Do not seek treatment metadata.",
        },
    )
    if RUBRIC_PATH.is_file():
        shutil.copy2(RUBRIC_PATH, public_out / "RUBRIC.md")
    shutil.copy2(SCORER_SCHEMA_PATH, public_out / "scorer.schema.json")
    template_lines: list[str] = []
    for row in blind_rows:
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
    write_json(
        private_map,
        {
            "schema_version": "1.0",
            "run_dir": str(run_dir),
            "public_bundle": str(public_out),
            "fixture_hashes": allocation["fixture_hashes"],
            "trials": blind_rows,
        },
    )
    return {
        "public_bundle": str(public_out),
        "private_map": str(private_map),
        "packet_count": len(blind_rows),
        "invalid_response_count": sum(
            1
            for row in blind_rows
            if load_json(public_out / "packets" / f"{row['blind_id']}.json")["response_validation_errors"]
        ),
    }


def read_score_files(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            records.append(item)
    return records


def validate_score_record(record: Any, packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_keys = {"schema_version", "scorer_id", "blind_id", "case_id", "ratings", "critical_failures", "rationale"}
    if not isinstance(record, dict) or set(record) != expected_keys:
        return ["score record has the wrong top-level shape"]
    if record.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not isinstance(record.get("scorer_id"), str) or not record["scorer_id"].strip() or record["scorer_id"] == "REPLACE":
        errors.append("scorer_id must be set")
    if record.get("blind_id") != packet["blind_id"]:
        errors.append("blind_id does not match packet")
    if record.get("case_id") != packet["case_id"]:
        errors.append("case_id does not match packet")
    ratings = record.get("ratings")
    applicable = set(packet["applicable_rubric_dimensions"])
    if not isinstance(ratings, dict) or set(ratings) != set(DIMENSIONS):
        errors.append("ratings has the wrong shape")
    else:
        for dimension in DIMENSIONS:
            rating = ratings[dimension]
            if dimension in applicable:
                if not is_int(rating) or not 0 <= rating <= 4:
                    errors.append(f"{dimension} must be an integer from 0 to 4")
            elif rating is not None:
                errors.append(f"{dimension} must be null because it is not applicable")
    failures = record.get("critical_failures")
    if not isinstance(failures, list) or len(failures) != len(set(failures)) or set(failures) - CRITICAL_CODES:
        errors.append("critical_failures contains invalid or duplicate codes")
    if not isinstance(record.get("rationale"), str) or not record["rationale"].strip() or record["rationale"] == "REPLACE":
        errors.append("rationale must be set")
    return errors


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
            if not okay:
                failures.append("FABRICATED_PROVENANCE")
            if excerpt is not None:
                failures.append("PRIVATE_DISCLOSURE")
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


def aggregate_scores(public_bundle: Path, private_map: Path, score_paths: list[Path], seed: int) -> dict[str, Any]:
    public_bundle = public_bundle.resolve()
    mapping = load_json(private_map.resolve())
    map_by_blind = {row["blind_id"]: row for row in mapping["trials"]}
    packets = {
        blind_id: load_json(public_bundle / "packets" / f"{blind_id}.json")
        for blind_id in map_by_blind
    }
    records = read_score_files(score_paths)
    by_blind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    validation_errors: list[str] = []
    for record in records:
        blind_id = record.get("blind_id") if isinstance(record, dict) else None
        if blind_id not in packets:
            validation_errors.append(f"unknown blind_id in score record: {blind_id}")
            continue
        errors = validate_score_record(record, packets[blind_id])
        if errors:
            validation_errors.extend(f"{blind_id}/{record.get('scorer_id')}: {error}" for error in errors)
            continue
        identity = (record["scorer_id"], blind_id)
        if identity in seen:
            validation_errors.append(f"duplicate score from {record['scorer_id']} for {blind_id}")
            continue
        seen.add(identity)
        by_blind[blind_id].append(record)

    cases = case_index()
    oracles = oracle_index()
    trial_results: list[dict[str, Any]] = []
    rating_pairs = 0
    within_one = 0
    unresolved_disagreements: list[str] = []
    dimension_values_by_condition: dict[str, dict[str, list[float]]] = {
        "baseline": defaultdict(list),
        "skill": defaultdict(list),
    }
    for blind_id, allocation in map_by_blind.items():
        packet = packets[blind_id]
        graders = by_blind.get(blind_id, [])
        if len(graders) < 2:
            validation_errors.append(f"{blind_id}: fewer than two independent scorer records")
        dimension_scores: dict[str, float] = {}
        for dimension in packet["applicable_rubric_dimensions"]:
            values = [float(record["ratings"][dimension]) for record in graders]
            for left in range(len(values)):
                for right in range(left + 1, len(values)):
                    rating_pairs += 1
                    if abs(values[left] - values[right]) <= 1:
                        within_one += 1
            if len(values) == 2 and abs(values[0] - values[1]) >= 2:
                unresolved_disagreements.append(f"{blind_id}:{dimension}")
            if values:
                dimension_scores[dimension] = float(statistics.median(values)) if len(values) >= 3 else statistics.fmean(values)
                dimension_values_by_condition[allocation["condition"]][dimension].append(dimension_scores[dimension])
        qualitative = 0.0
        if dimension_scores:
            qualitative = 40.0 * statistics.fmean(dimension_scores.values()) / 4.0

        critical_votes: Counter[str] = Counter()
        for record in graders:
            critical_votes.update(record["critical_failures"])
        judge_critical: list[str] = []
        for code, count in critical_votes.items():
            if len(graders) >= 3:
                if count >= math.ceil(len(graders) / 2):
                    judge_critical.append(code)
            elif count > 0:
                judge_critical.append(code)
        if len(graders) == 2 and bool(graders[0]["critical_failures"]) != bool(graders[1]["critical_failures"]):
            unresolved_disagreements.append(f"{blind_id}:critical-failure")

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
                "total_score": round(total, 3),
                "trial_pass": total >= 75.0 and not all_critical,
                "scorer_count": len(graders),
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
    ci_low, ci_high = hierarchical_bootstrap(differences, seed)
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
    }
    skill_dimensions = condition_summary["skill"]["dimension_means"]
    primary_floor_ok = all(skill_dimensions.get(dimension, 0.0) >= 3.0 for dimension in primary_dimensions)
    no_bad_case = all(summary["median"] >= 70.0 for summary in skill_case_summaries.values())
    no_negative_case_lift = all(value >= -5.0 for value in case_lifts.values())

    gates = {
        "protocol_integrity": {
            "minimum_five_replicates_per_case_condition": minimum_replicates >= 5,
            "two_or_more_scorers_per_response": all(row["scorer_count"] >= 2 for row in trial_results),
            "no_unresolved_large_scorer_disagreement": not unresolved_disagreements,
            "within_one_interrater_rate_at_least_0_80": reliability >= 0.80,
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

    return {
        "schema_version": "1.0",
        "status": {
            "protocol_valid": protocol_pass,
            "skill_behavioral_floor_pass": absolute_pass,
            "skill_incremental_lift_pass": lift_pass,
        },
        "validation_errors": validation_errors,
        "unresolved_scorer_disagreements": unresolved_disagreements,
        "interrater": {
            "rating_pair_count": rating_pairs,
            "within_one_count": within_one,
            "within_one_rate": round(reliability, 4),
        },
        "condition_summary": condition_summary,
        "skill_case_summary": skill_case_summaries,
        "paired_effect": {
            "pair_count": len(paired),
            "mean_lift": round(mean_lift, 3),
            "hierarchical_bootstrap_95pct_ci": [round(ci_low, 3), round(ci_high, 3)],
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

    score = subparsers.add_parser("score", help="Combine deterministic checks with two-or-more blind scorer files")
    score.add_argument("--public-bundle", type=Path, required=True)
    score.add_argument("--private-map", type=Path, required=True)
    score.add_argument("--scores", type=Path, action="append", required=True)
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
        elif args.command == "score":
            result = aggregate_scores(args.public_bundle, args.private_map, args.scores, args.seed)
            write_json(args.out.resolve(), result)
        else:  # pragma: no cover
            raise ValueError(f"Unsupported command: {args.command}")
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
