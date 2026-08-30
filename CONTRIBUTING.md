# Contributing

Contributions that make the research more auditable, conservative, portable, or reproducible are welcome. Please open a focused issue before proposing a new data source, schema change, or scoring rule so the evidence and compatibility tradeoffs are explicit.

## Development setup

The runtime and tests use only the Python standard library. Use Python 3.10 or newer from the repository root:

```text
python -m py_compile scripts/community_signal.py
python -m unittest discover -s tests -p "test_*.py" -v
python .github/scripts/smoke_cli.py
```

If Codex's bundled skill validator is installed, also run:

```text
python /path/to/skill-creator/scripts/quick_validate.py .
```

CI independently runs the open Agent Skills reference validator and exercises Python 3.10, 3.12, and 3.14 on Linux, Windows, and macOS.

## Pull requests

- Keep the helper standard-library-only, offline, and deterministic. A proposal to add a runtime dependency needs a concrete reliability or safety benefit.
- Add behavior-level tests for each fix. Include adversarial cases when changing deduplication, quote binding, eligibility, willingness-to-pay, coverage, or artifact-integrity rules.
- Use synthetic handles and synthetic discussion text in fixtures. Do not commit real personal data, authentication material, private exports, study `.author-key` files, or unpublished research artifacts.
- Preserve compatibility with the Agent Skills specification. Keep `SKILL.md` routing concise and put conditional detail in the appropriate reference file.
- Document user-visible schema or behavior changes. Do not silently weaken an audit gate to make a fixture pass.
- Keep generated artifacts out of source changes unless a maintained example explicitly requires them.

By submitting a contribution, you agree that it may be distributed under this repository's MIT License.

Report suspected vulnerabilities or privacy failures through the private process in `SECURITY.md`, not a public issue.
