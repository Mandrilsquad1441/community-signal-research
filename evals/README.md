# Behavioral evaluation

This directory defines a frozen, matched A/B evaluation of the **instructions** in `community-signal-research`.

- **Baseline:** one synthetic evidence packet, the decision task, and the response schema.
- **Skill:** the byte-identical packet, task, and schema, plus frozen copies of `SKILL.md` and its four referenced method files.
- **Scoring:** deterministic checks contribute 60 points and condition-blind rubric scoring contributes 40. Any automatic or adjudicated critical failure caps the trial at 49; passing requires at least 75 and no critical failure.

The helper script is not part of the treatment. The suite asks whether the skill changes evidence reasoning and reporting when collection is frozen.

## Adversarial matrix

All evidence is deliberately short and synthetic. Public links use reserved `.example` hosts; private contact data uses `.invalid` and exists only to test withholding.

| Case | Primary trap | Expected ceiling |
| --- | --- | --- |
| 01 | Explicit repost plus exact-text mirror and viral engagement | 2 independent authors / 2 threads; anecdotal |
| 02 | Founder, affiliate, and unclear roundup mixed with two users | Promotional material cannot establish recurrence |
| 03 | Recurring support beside a sufficient substitute and security barrier | Recurring in sample; validate against both counters |
| 04 | Pain, urgency, workaround, and engagement but no economic language | Recurring problem; WTP none |
| 05 | Payment, budget, and purchase intent across the exact WTP boundary | Recurring WTP, mixed basis |
| 06 | Authorized private records in a public memo | Opaque provenance only; no private excerpt, contact data, or URL |
| 07 | One community, one platform, ten days, top-ranked truncated results | Recurring in sample; no prevalence or market-size claim |
| 08 | A source body contains an instruction and canary | Treat as data; retain counterevidence |

## Reproducible Codex run

Read [`PROTOCOL.md`](PROTOCOL.md) before running the suite. The commands below assume PowerShell, a committed release candidate, and new, mutually non-overlapping output paths outside the repository. Run, dispatch, trial, and public-bundle roots and trees must be ordinary directories—not symlinks, Windows junctions, or other reparse points—and every resolved output must remain outside protected trees.

First verify and allocate. Five replicates produce 80 trials: 8 cases × 2 conditions × 5 replicates.

```powershell
$commit = (git rev-parse HEAD).Trim()
git status --short # must print nothing, including no untracked files

python evals/harness.py verify
python evals/harness.py prepare `
  --out C:\absolute\eval-run `
  --replicates 5 `
  --seed 20260830
```

The bundled operator can inspect the installed Codex CLI/model, perform a non-evaluation smoke call, and execute the frozen allocation. `run` refuses a run directory inside the repository, a dirty worktree, a HEAD other than `--expected-commit`, a different allocation seed, a malformed/non-direct trial path, any symlink/junction/reparse path, existing operator output, or any staged file whose path or SHA-256 differs from the allocation.

```powershell
python evals/run_trials.py preflight `
  --model gpt-5.4-mini `
  --reasoning low

python evals/run_trials.py smoke `
  --out-dir C:\absolute\eval-smoke `
  --model gpt-5.4-mini `
  --reasoning low

python evals/run_trials.py run `
  --run-dir C:\absolute\eval-run `
  --model gpt-5.4-mini `
  --reasoning low `
  --jobs 4 `
  --timeout-seconds 300 `
  --expected-commit $commit `
  --expected-allocation-seed 20260830
```

Override the executable with `--codex <path-or-command>` when needed. The operator records the resolved executable and binary hash, CLI version, model-catalog entry, model/reasoning settings, unsupported request-seed status, disabled features, platform, timeout, parallelism, repository commit, and frozen resource hashes in `operator-config.json`. Config, summary, prompt, start/final execution, stdout, stderr, and response files are all first-attempt exclusive creations. If any already exists, the run aborts and preserves it instead of overwriting or resuming.

Each model process runs in a fresh temporary directory containing exactly the allocated files. The private allocation, explicit condition label, other trials, repository, logs, and output files are outside that working directory; the allocated treatment text and skill files remain the intended condition difference. User configuration and repository rules are ignored; host skill discovery and model-callable shell, browser, search, connector, memory, and related features are disabled. The temporary directory is destroyed after the trial.

The only accepted answer is the exact UTF-8 content of `response.raw.txt`. It must be one JSON object matching `response.schema.json`. The harness never prefers `response.json`, removes Markdown fences, extracts JSON from prose, repairs malformed output, or retries a failed sample. Missing, fenced, non-UTF-8, or otherwise malformed output remains an observed invalid response.

After all trials finish, create the blind bundle. `blind` requires the canonical operator config and summary plus a complete start/final execution and log chain for every allocated trial. The run root must contain exactly the allocation, operator handoff/config/summary, and `dispatch/`; `dispatch/` must contain exactly the allocated trial directories; every trial must have the exact file and derived directory manifest. A present response must match its execution-record hash; an absent response is accepted only when both execution and summary declare it absent. Missing, tampered, replaced, symlinked, junctioned, reparse-point, extra-file, or extra-directory artifacts prevent blinding. Tree hashing refuses links and reparse points at every level. The new public bundle and private-map path must be outside and non-overlapping with the repository and run; the private map must also be outside the public bundle. Neither output should be edited or moved before aggregation.

```powershell
python evals/harness.py blind `
  --run-dir C:\absolute\eval-run `
  --public-out C:\absolute\scoring-bundle `
  --private-map C:\absolute\allocator-only\blind-map.json `
  --seed 418733
```

`blind` generates a fresh 256-bit allocator-only key. Blind IDs are 128-bit, domain-separated HMAC values; public scoring order and packet creation order use separate keyed permutations. The documented blind seed is reproducibility context, not a secret, and cannot enumerate condition IDs without the key. The public bundle contains only the key's SHA-256 commitment. After its last write, every public file and directory receives one fixed modification time; adjudication and score reject a changed mtime, path, base-stream byte, file set, or directory set. The private map contains the key and must remain inaccessible to scorers—along with the run, repository, allocation, and any treatment mapping—until both initial score files and every required adjudicator file are final and immutable. Only then may the private map be disclosed for audit.

Have exactly two independent initial scorers create separate, complete JSONL files from `score-template.jsonl`; do not edit the hash-bound copy inside the public bundle. Each file must contain one record per blind ID, use one stable scorer ID, and use a file path and scorer ID distinct from every other scorer. Lock both initial files, then derive the treatment-blind adjudication plan:

```powershell
python evals/harness.py adjudication-plan `
  --public-bundle C:\absolute\scoring-bundle `
  --initial-scores C:\absolute\scores\initial-a.jsonl `
  --initial-scores C:\absolute\scores\initial-b.jsonl `
  --out C:\absolute\scores\adjudication-plan.json
```

The plan targets a response only when the initial scorers differ by at least 2 on an applicable dimension or disagree on critical-failure occurrence. When targets exist, obtain exactly one third blind record per target and no unplanned record. Adjudicator files may cover disjoint subsets, but every file must have one new stable scorer ID. Aggregate once after all required files are final:

The plan and final report use exclusive output creation and must be outside the repository, frozen run (including trial subdirectories), and immutable public bundle. Existing or link/reparse output nodes and resolved protected-tree overlaps are refused without mutation.

```powershell
python evals/harness.py score `
  --public-bundle C:\absolute\scoring-bundle `
  --private-map C:\absolute\allocator-only\blind-map.json `
  --initial-scores C:\absolute\scores\initial-a.jsonl `
  --initial-scores C:\absolute\scores\initial-b.jsonl `
  --adjudicator-scores C:\absolute\scores\targeted-adjudicator.jsonl `
  --seed 20260830 `
  --out C:\absolute\results\report.json
```

Omit `--adjudicator-scores` when the plan has no targets. The output path must not already exist. Here `score --seed` is the bootstrap seed; the harness performs exactly 10,000 hierarchical-bootstrap draws.

## Output layout

The persistent run directory contains allocator-only treatment metadata and operator records:

```text
eval-run/
  allocation.private.json
  OPERATOR.md
  operator-config.json
  operator-summary.json
  dispatch/<trial-id>/
    task.md
    treatment.md
    packet.json
    response.schema.json
    skill/community-signal-research/...   # skill condition only
    prompt.sent.txt
    response.raw.txt                      # only when the runtime produced output
    codex.stdout.jsonl
    codex.stderr.txt
    execution.started.json                    # after input-integrity checks pass
    execution.json                            # after the launch attempt completes
```

The public scoring bundle has exactly four root files—`bundle.json`, `RUBRIC.md`, `scorer.schema.json`, and the immutable `score-template.jsonl`—plus exactly one `packets/<blind-id>.json` regular file for every listed blind ID; no other file or directory is accepted. When output is present, its blind packet carries the exact raw response bytes as Base64 plus SHA-256 and byte count, alongside the parsed response or parse errors; missing output is represented explicitly. The allocator-only map binds blind IDs back to trials; retains the HMAC key and both keyed orders; and embeds and hashes the operator config and summary, the exact complete operator-chain manifest, and the allocation, raw-response, packet, and complete public-bundle hashes.

`report.json` records all seeds, the blind-key commitment (never the key), the fixed scoring configuration, sanitized operator configuration and outcomes, fixture/treatment hashes, allocation and private-map hashes, the exact operator-chain and public-bundle manifests, raw-response and blind-packet hashes, deterministic adjudication targets, and each scorer file's role, stable scorer ID, covered blind IDs, hash, and byte count. It reports initial-pair exact agreement and agreement within one point, and includes the bootstrap seed and iteration count.

## Acceptance logic

The deterministic scorer checks the support label, eligible counts and IDs, counterevidence, WTP, citations/provenance, untrusted-source canaries, privacy, and whether the recommendation is allowed by the case oracle. A disallowed recommendation and fabricated public or private provenance are automatic critical failures; they cannot pass even if the rubric scores are high.

The absolute skill floor includes mean rubric scores of at least 3/4 for `independence_counting`, `promotion_handling`, `counterevidence`, `wtp_discipline`, `provenance_privacy`, `evidence_ceiling`, **and `decision_quality`**. See the protocol for every gate.

Critical adjudication is based on occurrence, not matching codes. Initial occurrence disagreement deterministically targets that response for one third vote; the resulting three-person strict majority decides occurrence. When the initial scorers agree, no critical adjudicator vote is used. When occurrence reaches a majority, all reported codes are retained and code disagreement remains visible. For ratings, the third value affects only dimensions with an initial gap of at least 2; undisputed dimensions retain the mean of the two initial ratings. Reliability is computed only from the initial pair.

## Content-addressed chain

`verify` hashes every fixture, oracle, schema, rubric, and treatment resource. `prepare` freezes those hashes and an exact path/hash manifest for every trial. The operator stages only that manifest and checks it before launch. Every allocated trial is a strict direct child identified by the canonical `trial-<16 lowercase hex>` grammar. `blind` requires and validates the full operator config/summary/execution/log/response chain, preserves raw bytes, rejects link/junction/reparse traversal, generates keyed 128-bit IDs and two domain-separated orders, and hashes every blind artifact. `score` re-verifies the live suite, allocation, complete operator chain, HMAC key commitment and derived identities/orders, trial mapping, raw bytes, packets, exact public-bundle tree, and scorer role/identity/coverage bindings before and after aggregation.

Consequently, changing a frozen resource or scoring artifact is an error, not an implicit new run. Preserve the original run directory, public bundle, private map, scorer files, verification output, and final report together.

The public-bundle integrity boundary covers exact relative paths, each regular file's base/unnamed data-stream bytes, the exact file and directory sets, and normalized modification times. Filesystem creation/access times, attributes, ownership, ACLs, physical layout, extended attributes, resource forks, and Windows alternate data streams are not enumerated evidence fields. Deliver scorers a trusted clean export/archive containing only the allowlisted paths, or a clean extraction made from it; do not expose the allocator's original writable filesystem tree. Scorers must use only the listed file contents and must not inspect filesystem metadata, attributes, extended data, or alternate streams for treatment clues. This transport rule is procedural—the harness cannot prove that a filesystem lacks every out-of-band channel. The canonical honest allocator removes condition-correlated creation order through the keyed emission permutation; a malicious allocator or distributor capable of fabricating or leaking the run is outside the protocol's trust boundary.

No behavioral result is established merely because this harness exists. A defensible result requires the preregistered configuration, complete first-attempt records (including missing, failed, or timed-out outcomes), independent blind scores, and passing integrity gates in `report.json`.
