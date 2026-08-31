#!/usr/bin/env python3
"""Reproducible Codex CLI operator for frozen behavioral-evaluation trials.

This script is deliberately separate from scoring. It reads the allocator's
private dispatch order, inlines only the files staged for one trial, launches a
fresh ephemeral `codex exec` process with model tools disabled, and preserves
the first raw final output and event logs. It never reads evaluation oracles.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVAL_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parent
RESPONSE_SCHEMA_PATH = EVAL_ROOT / "schemas" / "response.schema.json"
OPERATOR_VERSION = "1.4"
TRIAL_ID_RE = re.compile(r"^trial-[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_SEED_STATUS = (
    "unsupported: captured Codex exec help exposed no --seed or --request-seed "
    "option; allocation model_seed recorded but not applied"
)
MODEL_SEED_NOTE = (
    "Not applied; this batch's captured Codex exec help exposed no --seed "
    "or --request-seed option."
)

SMOKE_EXPECTED_RESPONSE = {
    "schema_version": "1.0",
    "case_id": "case-00-smoke",
    "signal_id": "sig-smoke",
    "recommendation": "insufficient_evidence",
    "support_assessment": "unsupported",
    "independent_support": {"authors": 0, "threads": 0, "source_ids": []},
    "excluded_or_collapsed_sources": [],
    "counterevidence": {
        "status": "not_established",
        "source_ids": [],
        "summary": "Smoke test only.",
    },
    "wtp": {
        "level": "none",
        "basis": "none",
        "source_ids": [],
        "summary": "No evidence was supplied.",
    },
    "public_memo": "This is only an operator smoke test.",
    "citations": [
        {
            "source_id": "src-smoke",
            "visibility": "public",
            "locator": "https://example.com/smoke",
            "source_file_sha256": None,
            "excerpt": "Smoke test only.",
        }
    ],
    "limitations": ["No research evidence was supplied."],
    "next_test": "Run the frozen evaluation only after this smoke test passes.",
}

CORE_FILES = (
    Path("task.md"),
    Path("treatment.md"),
    Path("packet.json"),
    Path("response.schema.json"),
)

TREATMENT_FILES = (
    Path("skill/community-signal-research/SKILL.md"),
    Path("skill/community-signal-research/references/method.md"),
    Path("skill/community-signal-research/references/scoring.md"),
    Path("skill/community-signal-research/references/data-contracts.md"),
    Path("skill/community-signal-research/references/source-playbooks.md"),
)

# Every feature in this list is disabled for both conditions. The packet and
# treatment are supplied inline, so the agent needs no model-callable tools.
DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "shell_snapshot",
    "shell_snapshot_v2",
    "shell_tool",
    "skill_search",
    "sleep_tool",
    "tool_suggest",
    "view_image",
    "workspace_dependencies",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json_exclusive(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def write_text_exclusive(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)


def write_bytes_exclusive(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_json_value(value: Any) -> str:
    """Serialize a JSON value so comparisons preserve every JSON scalar type."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def extract_request_seed_options(help_text: str) -> list[str]:
    """Return request-seed flags explicitly advertised by ``codex exec --help``."""
    return sorted(
        set(re.findall(r"(?<![\w-])--(?:request-)?seed(?![\w-])", help_text))
    )


def require_request_seed_unsupported(identity: dict[str, Any]) -> None:
    if (
        not isinstance(identity, dict)
        or "request_seed_options" not in identity
        or not isinstance(identity["request_seed_options"], list)
        or any(not isinstance(option, str) or not option for option in identity["request_seed_options"])
    ):
        raise ValueError("Codex executable identity has malformed request-seed capability data")
    options = identity["request_seed_options"]
    if options:
        rendered = ", ".join(str(option) for option in options)
        raise ValueError(
            "Codex exec now exposes request-seed option(s) "
            f"{rendered}; update the operator to apply every allocated model seed "
            "before running this evaluation"
        )


def require_executable_hash(command_path: str, expected_sha256: str, label: str) -> None:
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ValueError(f"{label} expected hash is malformed")
    executable = require_regular_file(Path(command_path), label)
    actual_sha256 = sha256_file(executable)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{label} hash changed: expected {expected_sha256}, observed {actual_sha256}"
        )


def executable_identity(command: str) -> dict[str, Any]:
    resolved = shutil.which(command)
    if resolved is None:
        raise ValueError(f"Executable not found: {command}")
    executable = Path(resolved).resolve(strict=True)
    binary_sha256 = sha256_file(executable)
    completed = subprocess.run(
        [str(executable), "--version"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(f"Unable to obtain Codex version: {completed.stderr.strip()}")
    require_executable_hash(str(executable), binary_sha256, "Codex executable after version probe")
    version_output = (completed.stdout + "\n" + completed.stderr).strip()
    if not version_output:
        raise ValueError("Unable to obtain Codex version: command returned no version text")
    help_completed = subprocess.run(
        [str(executable), "exec", "--help"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
    )
    if help_completed.returncode != 0:
        raise ValueError(f"Unable to obtain Codex exec help: {help_completed.stderr.strip()}")
    require_executable_hash(str(executable), binary_sha256, "Codex executable after help probe")
    help_bytes = (
        help_completed.stdout.encode("utf-8")
        + b"\x00"
        + help_completed.stderr.encode("utf-8")
    )
    help_text = help_completed.stdout + "\n" + help_completed.stderr
    return {
        "requested_command": command,
        "resolved_path": str(executable),
        "binary_sha256": binary_sha256,
        "version_output": version_output,
        "exec_help_sha256": sha256_bytes(help_bytes),
        "request_seed_options": extract_request_seed_options(help_text),
    }


def model_catalog_entry(codex_path: str, model: str) -> tuple[dict[str, Any], str]:
    completed = subprocess.run(
        [codex_path, "debug", "models"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError(f"Unable to read Codex model catalog: {completed.stderr.strip()}")
    catalog = json.loads(completed.stdout)
    matching = [item for item in catalog.get("models", []) if item.get("slug") == model]
    if len(matching) != 1:
        raise ValueError(f"Model {model!r} was not found exactly once in the catalog")
    item = matching[0]
    selected = {
        "slug": item.get("slug"),
        "display_name": item.get("display_name"),
        "description": item.get("description"),
        "default_reasoning_level": item.get("default_reasoning_level"),
        "supported_reasoning_levels": [entry.get("effort") for entry in item.get("supported_reasoning_levels", [])],
        "context_window": item.get("context_window"),
        "max_context_window": item.get("max_context_window"),
        "tool_mode": item.get("tool_mode"),
    }
    return selected, sha256_bytes(completed.stdout.encode("utf-8"))


def git_snapshot(repo_root: Path) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, encoding="utf-8",
        capture_output=True, check=False, timeout=30,
    )
    status = subprocess.run(
        ["git", "status", "--short"], cwd=repo_root, text=True, encoding="utf-8",
        capture_output=True, check=False, timeout=30,
    )
    if head.returncode != 0 or status.returncode != 0:
        raise ValueError("Unable to record repository state")
    return {"head": head.stdout.strip(), "status_short": status.stdout.splitlines()}


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_link_or_reparse(path: Path) -> bool:
    """Identify symlinks, junctions, and other Windows reparse points."""
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


def require_real_directory(path: Path, label: str) -> Path:
    if is_link_or_reparse(path) or not path.is_dir():
        raise ValueError(f"{label} must be a real directory, not a link, junction, or reparse point")
    return path.resolve(strict=True)


def require_regular_file(path: Path, label: str) -> Path:
    if is_link_or_reparse(path):
        raise ValueError(f"{label} must not be a link, junction, or reparse point")
    try:
        mode = path.stat().st_mode
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file")
    return resolved


def require_trial_directory(run_dir: Path, trial_id: Any) -> Path:
    if not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id):
        raise ValueError("Allocation contains an unsafe trial_id")
    run_root = require_real_directory(run_dir, "Run directory")
    dispatch_root = require_real_directory(run_root / "dispatch", "Dispatch directory")
    candidate = dispatch_root / trial_id
    trial_root = require_real_directory(candidate, f"{trial_id} trial directory")
    if trial_root.parent != dispatch_root:
        raise ValueError(f"{trial_id}: trial directory must be a direct child of dispatch")
    return trial_root


def child_file_state(root: Path, relative: Path, *, required: bool) -> bool:
    """Validate one fixed relative file path without traversing linked parents."""
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe staged path: {relative.as_posix()}")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if is_link_or_reparse(current):
            raise ValueError(f"Staged parent is a link, junction, or reparse point: {relative.as_posix()}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"Staged parent is not a directory: {relative.as_posix()}")
    candidate = root / relative
    if is_link_or_reparse(candidate):
        raise ValueError(f"Staged file is a link or reparse point: {relative.as_posix()}")
    if not candidate.exists():
        if required:
            raise ValueError(f"{root.name}: missing core file {relative.as_posix()}")
        return False
    if not candidate.is_file():
        raise ValueError(f"Staged path is not a regular file: {relative.as_posix()}")
    resolved = candidate.resolve(strict=True)
    if not is_within(resolved, root) or resolved == root:
        raise ValueError(f"Staged file escapes its trial directory: {relative.as_posix()}")
    return True


def require_fresh_trial_outputs(trial_dir: Path) -> None:
    names = (
        "execution.json",
        "execution.started.json",
        "prompt.sent.txt",
        "response.raw.txt",
        "response.json",
        "codex.stdout.jsonl",
        "codex.stderr.txt",
    )
    if any((trial_dir / name).exists() or is_link_or_reparse(trial_dir / name) for name in names):
        raise ValueError(f"{trial_dir.name}: output path already exists; refusing to overwrite")


def validate_allocation(allocation: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(allocation, dict):
        raise ValueError("Allocation must be one JSON object")
    trials = allocation.get("trials")
    order = allocation.get("dispatch_order")
    if not isinstance(trials, list) or not all(isinstance(trial, dict) for trial in trials):
        raise ValueError("Allocation trials must be a list of objects")
    trial_ids: list[str] = []
    core_paths = {path.as_posix() for path in CORE_FILES}
    skill_paths = core_paths | {path.as_posix() for path in TREATMENT_FILES}
    for trial in trials:
        trial_id = trial.get("trial_id")
        if not isinstance(trial_id, str) or not TRIAL_ID_RE.fullmatch(trial_id):
            raise ValueError("Allocation contains an unsafe trial_id")
        trial_ids.append(trial_id)
        if (
            not isinstance(trial.get("case_id"), str)
            or not isinstance(trial.get("pair_id"), str)
            or isinstance(trial.get("replicate"), bool)
            or not isinstance(trial.get("replicate"), int)
            or trial["replicate"] < 1
            or isinstance(trial.get("model_seed"), bool)
            or not isinstance(trial.get("model_seed"), int)
            or trial.get("condition") not in {"baseline", "skill"}
        ):
            raise ValueError(f"{trial_id}: allocation identity fields are malformed")
        hashes = trial.get("trial_file_hashes")
        if not isinstance(hashes, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) and SHA256_RE.fullmatch(digest)
            for path, digest in hashes.items()
        ):
            raise ValueError(f"{trial_id}: allocation file hashes are malformed")
        expected_paths = core_paths if trial["condition"] == "baseline" else skill_paths
        if set(hashes) != expected_paths:
            raise ValueError(f"{trial_id}: allocation file paths do not match its condition")
    if len(trial_ids) != len(set(trial_ids)):
        raise ValueError("Allocation trial IDs must be unique")
    if not isinstance(order, list) or order != list(dict.fromkeys(order)) or set(order) != set(trial_ids):
        raise ValueError("Allocation dispatch order must be an exact trial permutation")
    return trials, order


def staged_files(trial_dir: Path) -> list[Path]:
    trial_dir = require_real_directory(trial_dir, f"{trial_dir.name} trial directory")
    for relative in CORE_FILES:
        child_file_state(trial_dir, relative, required=True)
    treatment_present = [
        relative for relative in TREATMENT_FILES
        if child_file_state(trial_dir, relative, required=False)
    ]
    if treatment_present and len(treatment_present) != len(TREATMENT_FILES):
        missing_treatment = [relative.as_posix() for relative in TREATMENT_FILES if not (trial_dir / relative).is_file()]
        raise ValueError(f"{trial_dir.name}: partial skill treatment; missing {missing_treatment}")
    return list(CORE_FILES) + treatment_present


def stage_trial(source_dir: Path, isolated_dir: Path) -> dict[str, str]:
    """Copy only the trial's explicitly allowed files into an empty directory."""
    if isolated_dir.exists() and any(isolated_dir.iterdir()):
        raise ValueError(f"Isolated staging directory is not empty: {isolated_dir}")
    isolated_dir.mkdir(parents=True, exist_ok=True)
    allowed = staged_files(source_dir)
    for relative in allowed:
        target = isolated_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / relative, target)
    return {
        path.relative_to(isolated_dir).as_posix(): sha256_file(path)
        for path in sorted(isolated_dir.rglob("*"))
        if path.is_file()
    }


def require_allocated_hashes(
    trial_id: str,
    expected: Any,
    actual: dict[str, str],
) -> None:
    """Require an exact path-and-content match to the allocator's trial manifest."""
    if not isinstance(expected, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in expected.items()
    ):
        raise ValueError(f"{trial_id}: allocation has an invalid trial_file_hashes map")
    if expected == actual:
        return
    expected_paths = set(expected)
    actual_paths = set(actual)
    missing = sorted(expected_paths - actual_paths)
    unexpected = sorted(actual_paths - expected_paths)
    changed = sorted(
        path for path in expected_paths & actual_paths if expected[path] != actual[path]
    )
    raise ValueError(
        f"{trial_id}: isolated staged file hashes do not exactly match allocation "
        f"(missing={missing}, unexpected={unexpected}, changed={changed})"
    )


def build_prompt(trial_dir: Path) -> tuple[str, dict[str, str]]:
    sections: list[str] = [
        "The following are the complete allowed files for this isolated evaluation trial. "
        "Use only their contents. No other files, tools, network sources, or prior context are available."
    ]
    hashes: dict[str, str] = {}
    for relative in staged_files(trial_dir):
        path = trial_dir / relative
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        label = relative.as_posix()
        hashes[label] = sha256_bytes(raw)
        sections.append(f"\n<allowed-file path={json.dumps(label)}>\n{text}\n</allowed-file>")
    sections.append(
        "\nComplete the task now. Return only the final JSON object required by response.schema.json. "
        "Do not mention this wrapper or the evaluation condition."
    )
    return "\n".join(sections), hashes


def codex_argv(
    codex_path: str,
    trial_dir: Path,
    response_path: Path,
    model: str,
    reasoning: str,
) -> list[str]:
    argv = [
        codex_path,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "-c",
        'model_verbosity="low"',
        "-c",
        'shell_environment_policy.inherit="none"',
        "-c",
        "suppress_unstable_features_warning=true",
        "--enable",
        "skip_host_skill_discovery",
    ]
    for feature in DISABLED_FEATURES:
        argv.extend(["--disable", feature])
    argv.extend(
        [
            "--cd",
            str(trial_dir),
            "--output-schema",
            str(trial_dir / "response.schema.json"),
            "--output-last-message",
            str(response_path),
            "--json",
            "--color",
            "never",
            "-",
        ]
    )
    return argv


def execute_trial(
    trial: dict[str, Any],
    run_dir: Path,
    codex_path: str,
    expected_codex_sha256: str,
    model: str,
    reasoning: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    trial_id = trial.get("trial_id")
    trial_dir = require_trial_directory(run_dir, trial_id)
    final_record = trial_dir / "execution.json"
    started_record = trial_dir / "execution.started.json"
    prompt_path = trial_dir / "prompt.sent.txt"
    response_path = trial_dir / "response.raw.txt"
    stdout_path = trial_dir / "codex.stdout.jsonl"
    stderr_path = trial_dir / "codex.stderr.txt"
    require_fresh_trial_outputs(trial_dir)

    # The model process never runs in the dispatch tree. The temporary working
    # directory contains only the explicitly allowed inputs, and is destroyed
    # after this single trial. Allocation, condition metadata, logs, and raw
    # output remain outside it.
    with tempfile.TemporaryDirectory(prefix=f"csr-eval-{trial_id}-") as temporary:
        isolated_dir = Path(temporary).resolve()
        allowed_hashes = stage_trial(trial_dir, isolated_dir)
        require_allocated_hashes(
            trial_id,
            trial.get("trial_file_hashes"),
            allowed_hashes,
        )
        prompt, prompt_hashes = build_prompt(isolated_dir)
        if prompt_hashes != allowed_hashes:  # pragma: no cover - defensive TOCTOU check
            raise ValueError(f"{trial_id}: isolated files changed while building prompt")

        # Re-resolve immediately before the first persistent write so a
        # replaced dispatch or trial directory cannot redirect output.
        if require_trial_directory(run_dir, trial_id) != trial_dir:
            raise ValueError(f"{trial_id}: trial directory changed during staging")
        require_fresh_trial_outputs(trial_dir)
        require_executable_hash(
            codex_path,
            expected_codex_sha256,
            f"Codex executable before {trial_id} launch",
        )
        argv = codex_argv(codex_path, isolated_dir, response_path, model, reasoning)
        write_text_exclusive(prompt_path, prompt)
        started = {
            "schema_version": "1.0",
            "operator_version": OPERATOR_VERSION,
            "trial_id": trial_id,
            "case_id": trial["case_id"],
            "pair_id": trial["pair_id"],
            "replicate": trial["replicate"],
            "condition": trial["condition"],
            "allocated_model_seed": trial.get("model_seed"),
            "model_seed_applied": False,
            "model_seed_note": MODEL_SEED_NOTE,
            "started_at": utc_now(),
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "allowed_file_hashes": allowed_hashes,
            "argv": argv,
        }
        write_json_exclusive(started_record, started)
        start_clock = time.monotonic()
        timed_out = False
        return_code: int | None = None
        launch_error: str | None = None
        with stdout_path.open("xb") as stdout_handle, stderr_path.open("xb") as stderr_handle:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=isolated_dir,
                    stdin=subprocess.PIPE,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=os.environ.copy(),
                )
                try:
                    require_executable_hash(
                        codex_path,
                        expected_codex_sha256,
                        f"Codex executable after {trial_id} launch",
                    )
                except Exception:
                    process.kill()
                    process.communicate()
                    raise
                try:
                    process.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    process.communicate()
                return_code = process.returncode
                require_executable_hash(
                    codex_path,
                    expected_codex_sha256,
                    f"Codex executable after {trial_id} completion",
                )
            except OSError as exc:
                launch_error = f"{type(exc).__name__}: {exc}"
    duration = time.monotonic() - start_clock
    response_present = child_file_state(trial_dir, Path("response.raw.txt"), required=False)
    record = {
        **started,
        "finished_at": utc_now(),
        "duration_seconds": round(duration, 3),
        "return_code": return_code,
        "timed_out": timed_out,
        "launch_error": launch_error,
        "response_present": response_present,
        "response_sha256": sha256_file(response_path) if response_present else None,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
    }
    write_json_exclusive(final_record, record)
    return {
        "trial_id": trial_id,
        "return_code": return_code,
        "timed_out": timed_out,
        "response_present": response_present,
        "duration_seconds": record["duration_seconds"],
    }


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    intended_run_dir = args.run_dir.resolve()
    if is_within(intended_run_dir, REPO_ROOT.resolve()):
        raise ValueError("Raw run directory must be outside the repository")
    if is_link_or_reparse(args.run_dir):
        raise ValueError("Raw run directory must not be a link, junction, or reparse point")
    run_dir = require_real_directory(args.run_dir, "Raw run directory")
    allocation_path = run_dir / "allocation.private.json"
    if not child_file_state(run_dir, Path("allocation.private.json"), required=False):
        raise ValueError(f"Missing allocation: {allocation_path}")
    allocation = load_json(allocation_path)
    trials, dispatch_order = validate_allocation(allocation)
    if trials:
        for trial in trials:
            trial_dir = require_trial_directory(run_dir, trial["trial_id"])
            _, actual_hashes = build_prompt(trial_dir)
            require_allocated_hashes(trial["trial_id"], trial["trial_file_hashes"], actual_hashes)
            require_fresh_trial_outputs(trial_dir)

    identity = executable_identity(args.codex)
    require_request_seed_unsupported(identity)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    repository = git_snapshot(REPO_ROOT)
    if repository["head"] != args.expected_commit:
        raise ValueError(f"Repository HEAD {repository['head']} != expected {args.expected_commit}")
    if repository["status_short"]:
        raise ValueError(
            "Repository worktree is dirty; commit or remove every tracked and untracked "
            f"change before evaluation: {repository['status_short']}"
        )
    if allocation.get("seed") != args.expected_allocation_seed:
        raise ValueError(
            f"Allocation seed {allocation.get('seed')} != expected {args.expected_allocation_seed}"
        )

    config_path = run_dir / "operator-config.json"
    summary_path = run_dir / "operator-summary.json"
    if any(path.exists() or is_link_or_reparse(path) for path in (config_path, summary_path)):
        raise ValueError(f"Operator config already exists; refusing a second batch: {config_path}")
    config = {
        "schema_version": "1.0",
        "operator_version": OPERATOR_VERSION,
        "operator_script": str(Path(__file__).resolve()),
        "operator_script_sha256": sha256_file(Path(__file__).resolve()),
        "created_at": utc_now(),
        "repository": repository,
        "expected_commit": args.expected_commit,
        "allocation_seed": allocation["seed"],
        "replicates": allocation["replicates"],
        "fixture_hashes": allocation["fixture_hashes"],
        "skill_resource_hashes": allocation["skill_resource_hashes"],
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "model": args.model,
        "reasoning_effort": args.reasoning,
        "model_verbosity": "low",
        "temperature": "unset; provider/CLI default",
        "top_p": "unset; provider/CLI default",
        "max_output_tokens": "unset; provider/CLI default",
        "request_seed": REQUEST_SEED_STATUS,
        "sandbox": "read-only",
        "network_search": False,
        "ephemeral": True,
        "ignore_user_config": True,
        "ignore_rules": True,
        "skip_host_skill_discovery": True,
        "disabled_features": list(DISABLED_FEATURES),
        "jobs": args.jobs,
        "timeout_seconds": args.timeout_seconds,
        "python": sys.version,
        "platform": platform.platform(),
        "trial_count": len(trials),
        "dispatch_order": dispatch_order,
    }
    write_json_exclusive(config_path, config)

    trial_by_id = {trial["trial_id"]: trial for trial in trials}
    ordered_trials = [trial_by_id[trial_id] for trial_id in dispatch_order]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                execute_trial,
                trial,
                run_dir,
                identity["resolved_path"],
                identity["binary_sha256"],
                args.model,
                args.reasoning,
                args.timeout_seconds,
            ): trial["trial_id"]
            for trial in ordered_trials
        }
        completed_count = 0
        for future in concurrent.futures.as_completed(futures):
            trial_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # preserve the batch and do not retry
                result = {
                    "trial_id": trial_id,
                    "operator_error": f"{type(exc).__name__}: {exc}",
                    "return_code": None,
                    "timed_out": False,
                    "response_present": False,
                }
            results.append(result)
            completed_count += 1
            print(
                json.dumps(
                    {
                        "progress": f"{completed_count}/{len(ordered_trials)}",
                        **result,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after batch",
    )
    summary = {
        "schema_version": "1.0",
        "finished_at": utc_now(),
        "trial_count": len(results),
        "response_count": sum(bool(item.get("response_present")) for item in results),
        "zero_exit_count": sum(item.get("return_code") == 0 for item in results),
        "timeout_count": sum(bool(item.get("timed_out")) for item in results),
        "operator_error_count": sum("operator_error" in item for item in results),
        "results": sorted(results, key=lambda item: item["trial_id"]),
    }
    write_json_exclusive(summary_path, summary)
    return summary


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    identity = executable_identity(args.codex)
    require_request_seed_unsupported(identity)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    return {
        "ok": True,
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "reasoning_effort": args.reasoning,
        "disabled_features": list(DISABLED_FEATURES),
        "note": (
            "The captured Codex exec help exposes no --seed or --request-seed option; "
            "allocated model seeds cannot be applied."
        ),
    }


def smoke_test(args: argparse.Namespace) -> dict[str, Any]:
    if is_link_or_reparse(args.out_dir):
        raise ValueError("Smoke output directory must not be a link, junction, or reparse point")
    if args.out_dir.exists() and not args.out_dir.is_dir():
        raise ValueError(f"Smoke output path is not a directory: {args.out_dir}")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty smoke directory: {args.out_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = require_real_directory(args.out_dir, "Smoke output directory")
    if is_within(out_dir, REPO_ROOT.resolve()):
        raise ValueError("Smoke output directory must be outside the repository")
    identity = executable_identity(args.codex)
    catalog_entry, catalog_hash = model_catalog_entry(identity["resolved_path"], args.model)
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after model-catalog probe",
    )
    if args.reasoning not in catalog_entry["supported_reasoning_levels"]:
        raise ValueError(f"Reasoning effort {args.reasoning!r} is unsupported for {args.model}")
    response_schema = require_regular_file(RESPONSE_SCHEMA_PATH, "Frozen evaluation response schema")
    write_bytes_exclusive(out_dir / "response.schema.json", response_schema.read_bytes())
    prompt = (
        "Return exactly this JSON object and nothing else: "
        + json.dumps(SMOKE_EXPECTED_RESPONSE, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    write_text_exclusive(out_dir / "prompt.sent.txt", prompt)
    response_path = out_dir / "response.raw.txt"
    argv = codex_argv(identity["resolved_path"], out_dir, response_path, args.model, args.reasoning)
    started = time.monotonic()
    completed = subprocess.run(
        argv,
        cwd=out_dir,
        input=prompt,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=args.timeout_seconds,
    )
    require_executable_hash(
        identity["resolved_path"],
        identity["binary_sha256"],
        "Codex executable after smoke call",
    )
    write_text_exclusive(out_dir / "codex.stdout.jsonl", completed.stdout)
    write_text_exclusive(out_dir / "codex.stderr.txt", completed.stderr)
    response_present = child_file_state(out_dir, Path("response.raw.txt"), required=False)
    response_matches_expected = False
    response_validation_error: str | None = None
    if response_present:
        try:
            response_value = json.loads(response_path.read_text(encoding="utf-8"))
            response_matches_expected = canonical_json_value(response_value) == canonical_json_value(
                SMOKE_EXPECTED_RESPONSE
            )
            if not response_matches_expected:
                response_validation_error = "response did not equal the requested smoke object"
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            response_validation_error = f"response was not one UTF-8 JSON value: {type(exc).__name__}"
    record = {
        "schema_version": "1.0",
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "codex": identity,
        "model_catalog_entry": catalog_entry,
        "model_catalog_raw_sha256": catalog_hash,
        "model": args.model,
        "reasoning_effort": args.reasoning,
        "argv": argv,
        "return_code": completed.returncode,
        "response_present": response_present,
        "response_matches_expected": response_matches_expected,
        "response_validation_error": response_validation_error,
        "response_sha256": sha256_file(response_path) if response_present else None,
        "stdout_sha256": sha256_file(out_dir / "codex.stdout.jsonl"),
        "stderr_sha256": sha256_file(out_dir / "codex.stderr.txt"),
    }
    write_json_exclusive(out_dir / "smoke.json", record)
    if completed.returncode != 0 or not response_present or not response_matches_expected:
        raise ValueError(f"Codex smoke failed; inspect {out_dir}")
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("preflight", help="Record CLI identity and confirm model/reasoning support")
    check.add_argument("--codex", default="codex")
    check.add_argument("--model", required=True)
    check.add_argument("--reasoning", required=True)

    smoke = subparsers.add_parser("smoke", help="Make one non-evaluation call with the exact isolated CLI flags")
    smoke.add_argument("--out-dir", type=Path, required=True)
    smoke.add_argument("--codex", default="codex")
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--reasoning", required=True)
    smoke.add_argument("--timeout-seconds", type=int, default=120)

    run = subparsers.add_parser("run", help="Execute every allocated trial exactly once")
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--codex", default="codex")
    run.add_argument("--model", required=True)
    run.add_argument("--reasoning", required=True)
    run.add_argument("--jobs", type=int, default=2)
    run.add_argument("--timeout-seconds", type=int, default=300)
    run.add_argument("--expected-commit", required=True)
    run.add_argument("--expected-allocation-seed", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(args)
        elif args.command == "smoke":
            result = smoke_test(args)
        elif args.command == "run":
            if args.jobs < 1:
                raise ValueError("jobs must be at least one")
            if args.timeout_seconds < 1:
                raise ValueError("timeout-seconds must be at least one")
            result = run_batch(args)
        else:  # pragma: no cover
            raise ValueError(f"Unknown command: {args.command}")
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
