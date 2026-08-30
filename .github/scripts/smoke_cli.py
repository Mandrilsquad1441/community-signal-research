#!/usr/bin/env python3
"""Small, deterministic end-to-end checks for the public CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "community_signal.py"


def run(*arguments: str, expected_code: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != expected_code:
        raise AssertionError(
            f"command {arguments!r} returned {result.returncode}, expected "
            f"{expected_code}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> None:
    version = run("--version").stdout.strip()
    if version != "community_signal.py 1.0.0":
        raise AssertionError(f"unexpected version output: {version!r}")

    raw_url = "HTTP://old.reddit.com/r/codex/comments/abc/title/?utm_source=ci#ignored"
    expected_url = "https://www.reddit.com/r/codex/comments/abc/title/"
    first = run("canonicalize", raw_url).stdout.strip()
    second = run("canonicalize", raw_url).stdout.strip()
    if first != expected_url or second != first:
        raise AssertionError(f"canonicalization was not deterministic: {first!r}, {second!r}")

    with tempfile.TemporaryDirectory(prefix="community-signal-smoke-") as temporary:
        study = Path(temporary) / "smoke-study"
        arguments = (
            "init",
            str(study),
            "--question",
            "Which recurring problem is evidenced?",
            "--decision",
            "Choose the next research step.",
            "--mode",
            "quick",
            "--as-of",
            "2026-08-30",
            "--recency-days",
            "90",
        )
        run(*arguments)
        plan_path = study / "study-plan.json"
        plan_before = plan_path.read_bytes()
        plan = json.loads(plan_before)
        if plan["as_of"] != "2026-08-30" or plan["mode"] != "quick":
            raise AssertionError("init did not preserve fixed CLI inputs")
        if not (study / ".author-key").is_file():
            raise AssertionError("init did not create the study-local author key")

        refusal = run(*arguments, expected_code=2)
        if "Refusing to overwrite" not in refusal.stderr:
            raise AssertionError("repeat init did not explain its overwrite refusal")
        if plan_path.read_bytes() != plan_before:
            raise AssertionError("repeat init modified an existing study")

    print("CLI smoke checks passed")


if __name__ == "__main__":
    main()
