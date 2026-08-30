# Contributing

Contributions that make the research more auditable, conservative, portable, or reproducible are welcome. Please open a focused issue before proposing a new data source, schema change, or scoring rule so the evidence and compatibility tradeoffs are explicit.

## Development and release gate

The core runtime and deterministic Python harness use only the standard library. Use Python 3.10 or newer from the repository root:

```text
python -m py_compile scripts/community_signal.py
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B -m unittest discover -s evals/tests -v
python -B evals/harness.py verify
python .github/scripts/smoke_cli.py
python scripts/community_signal.py audit examples/agent-skill-demand --strict --json
```

If Codex's bundled skill validator is installed, also run:

```text
python /path/to/skill-creator/scripts/quick_validate.py .
```

CI independently runs the open Agent Skills reference validator and exercises Python 3.10, 3.12, and 3.14 on Linux, Windows, and macOS.

Before a behavioral release run, commit the candidate, require an entirely clean worktree, and preflight the bundled operator with the exact intended model and reasoning setting:

```text
python evals/run_trials.py preflight --model gpt-5.4-mini --reasoning low
```

The core helper, unit suites, fixture verification, and CLI/example checks are offline. Operator preflight and the forward evaluation require an installed, authenticated Codex CLI and service connectivity; do not describe the whole evaluation suite as offline. Follow `evals/PROTOCOL.md` for commit binding, ordinary external directories (no symlinks, Windows junctions, or reparse points), exclusive first-attempt execution, blinding, and scoring.

## Pull requests

- Keep the helper standard-library-only, offline, and deterministic. A proposal to add a runtime dependency needs a concrete reliability or safety benefit.
- Add behavior-level tests for each fix. Include adversarial cases when changing deduplication, quote binding, eligibility, willingness-to-pay, coverage, or artifact-integrity rules.
- Use synthetic handles and synthetic discussion text in fixtures. Do not commit real personal data, authentication material, private exports, study `.author-key` files, or unpublished research artifacts.
- Preserve compatibility with the Agent Skills specification. Keep `SKILL.md` routing concise and put conditional detail in the appropriate reference file.
- Document user-visible schema or behavior changes. Do not silently weaken an audit gate to make a fixture pass.
- Keep generated artifacts out of source changes unless a maintained example explicitly requires them.

By submitting a contribution, you agree that it may be distributed under this repository's MIT License.

Report suspected vulnerabilities or privacy failures through the private process in `SECURITY.md`, not a public issue.
