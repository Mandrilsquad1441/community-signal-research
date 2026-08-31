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
| 09 | A capped pilot whose predeclared commitment rule is fully satisfied | `proceed` for only the bounded pilot; no rollout or market claim |
| 10 | Multiple unit URLs share threads, one author spans threads, and one row is a repost | Count 3 authors / 2 threads after collapse; WTP none |
| 11 | Private records change aggregates while containing materially different record-specific facts | Controlled aggregates and opaque provenance only; no semantic private disclosure |
| 12 | Public excerpts contain case, punctuation, and typography traps beside counterevidence | Exact literal substrings only; retain the counterevidence |

## Reproducible Codex run

Read [`PROTOCOL.md`](PROTOCOL.md) before running the suite. The commands below assume PowerShell, a committed release candidate, and new, mutually non-overlapping output paths outside the repository. Run, dispatch, trial, and public-bundle roots and trees must be ordinary directories—not symlinks, Windows junctions, or other reparse points—and every resolved output must remain outside protected trees.

First verify and allocate. Five replicates produce 120 trials: 12 cases × 2 conditions × 5 replicates.

```powershell
$commit = (git rev-parse HEAD).Trim()
git status --short # must print nothing, including no untracked files

python evals/harness.py verify
python evals/harness.py prepare `
  --out C:\absolute\eval-run `
  --replicates 5 `
  --seed 20260830
```

The bundled operator can inspect the installed Codex CLI/model, perform a non-evaluation smoke call, and execute the frozen allocation. The smoke call stages the byte-exact production `response.schema.json`, requests one fixed full response object, and passes only when Codex returns that exact JSON value; its new output directory preserves the prompt, raw response, event logs, hashes, and verdict. `run` refuses a run directory inside the repository, a dirty worktree, a HEAD other than `--expected-commit`, a different allocation seed, a malformed/non-direct trial path, any symlink/junction/reparse path, existing operator output, or any staged file whose path or SHA-256 differs from the allocation.

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
  --jobs 2 `
  --timeout-seconds 300 `
  --expected-commit $commit `
  --expected-allocation-seed 20260830
```

Override the executable with `--codex <path-or-command>` when needed. The operator records the resolved executable and binary hash, CLI version, a hash of captured `codex exec --help`, any detected `--seed`/`--request-seed` flags, the canonical selected-model entry and its binding hash, an informational hash of the volatile full model catalog, model/reasoning settings, disabled features, the structured host OS name plus platform string, timeout, parallelism, repository commit, and frozen resource hashes in `operator-config.json`. The verifier requires the OS name and containment mode to match the host performing the bound run verification. It hashes the executable before capability probes and rechecks that hash after version/help/model-catalog probes, immediately before and after every trial launch, after each trial completes, and after the batch. Any drift invalidates the first attempt. `preflight` and `run` also abort if captured help advertises either seed flag, because the current operator records allocated pair seeds but cannot yet apply one. Config, summary, prompt, start/final execution, stdout, stderr, and response files are all first-attempt exclusive creations. If any already exists, the run aborts and preserves it instead of overwriting or resuming.

Each model process runs in a fresh temporary directory containing exactly the allocated files. The private allocation, explicit condition label, other trials, repository, logs, and output files are outside that working directory; the allocated treatment text and skill files remain the intended condition difference. User configuration and repository rules are ignored; host and bundled skill-instruction injection plus model-callable shell, browser, search, connector, memory, and related features are disabled. A preflight `codex debug prompt-input` probe hashes the resulting model-visible message list and fails closed if it sees a host-skill catalog or skill path. This verifies prompt composition for the captured CLI/configuration; it is not a claim that the host filesystem is globally unreadable. The temporary directory is destroyed after the trial.

Run the batch directly in one attended foreground terminal. Do not use `Start-Process`, `Start-Job`, a detached shell, or a scheduler wrapper. Submission is lazy and bounded to `--jobs`, and the operator emits a heartbeat every ten seconds without a completion.

Before `Popen` can create a child, the operator inserts a pending launch slot into its shared registry and performs process creation/publication inside a short interrupt-deferred critical section. On Windows, every Codex CLI invocation—including capability probes and smoke—is then created with `CREATE_SUSPENDED`, assigned to a nested kill-on-close Job Object before target code can execute, and resumed only after the operator identifies the sole primary thread, validates that the thread still belongs to the new process, and requires `ResumeThread` to report the expected single-suspend count. Cleanup terminates the Job, reaps the root, polls Job Basic Accounting until `ActiveProcesses` is zero, and closes the Job handle. This is ordinary local process-tree cleanup for direct `CreateProcess` descendants while breakaway remains disabled. It is not a security sandbox against hostile same-user processes, and it cannot confine work delegated to an external process or service.

On POSIX, `start_new_session=True` gives the CLI a new session and original process group; cleanup sends `SIGKILL` to and polls only that original group. This contract is explicitly cooperative and records `escape_resistant=false`: the trusted CLI and its descendants must not re-session with `setsid`, re-group with `setpgid`, or delegate process creation to an external service. A descendant that does so can escape the recorded group. The Python standard library supplies no portable hard process-tree cage across the supported POSIX hosts, so none is claimed. Assignment or setup failure, termination failure, root-reap failure, failure to prove the recorded boundary drained, or containment-handle cleanup failure aborts the batch.

Config, sent prompt, start/final records, summary, and abort records are file-flushed in causal order. On POSIX, each durable exclusive evidence-file creation is followed by an `fsync` of its parent directory; logs and a present child-created response are file-flushed before the final record is written, whose parent-directory `fsync` seals that trial-directory state. On Windows, Python's `os.fsync` uses the CRT `_commit` operation for the file descriptor; the operator does not claim a separate parent-directory flush or stronger directory-entry durability there. On a catchable interruption, the operator requests cleanup of every shared-registry boundary, waits for worker shutdown, retries any boundary whose cleanup remains unproven, and writes exclusive `operator-abort.json` evidence. The same registry-empty assertion runs after normal worker shutdown. If cleanup is not provable, `operator-abort-cleanup.json` separately records the failures known when it is created without replacing the primary marker. A host power-off may prevent either marker; `operator-config.json` without a complete `operator-summary.json` is still permanently ineligible. Never resume, fill in, or reuse such a run—preserve it and start from a new commit, paths, and seeds.

The only accepted answer is the exact UTF-8 content of `response.raw.txt`. It must be one JSON object matching `response.schema.json`. That provider-facing schema intentionally uses the conservative strict Structured Outputs subset—required object shapes, explicit scalar/nullable types, and enums. The deterministic harness separately enforces the complete pattern, length, cardinality, uniqueness, cross-field, citation, and packet-identity contract, so unsupported provider keywords cannot silently weaken scoring. The harness never prefers `response.json`, removes Markdown fences, extracts JSON from prose, repairs malformed output, or retries a failed sample. Missing, fenced, non-UTF-8, or otherwise malformed output remains an observed invalid response.

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

The plan targets a response only when the initial scorers differ by at least 2 on an applicable dimension or disagree on critical-failure occurrence. It uses `schema_version: "2.0"` and `adjudication_contract_version: "2.0"`. Version 2 is the sparse contract: every treatment-blind target includes `case_id`, its public `packet_path`, the disputed dimensions, the critical-occurrence flag, and an explicit `record_template`. Replace every `REPLACE_...` placeholder; `scorer_id` and `rationale` values equal to `REPLACE` or beginning with `REPLACE_` are rejected. Set integers only for `disputed_dimensions` and leave **every other rating `null`**, including packet-applicable dimensions that are settled. If `critical_occurrence_disputed` is false, `critical_failures` must be `[]`; if true, replace its placeholder with `[]` for no critical failure or one or more codes from `scorer.schema.json` for a critical failure. Dense version-1 adjudicator records, which filled every packet-applicable rating, and version-1 plans are incompatible with sparse version 2 and must not be mixed.

When targets exist, obtain exactly one third blind record per target and no unplanned record. Adjudicator files may cover disjoint subsets, but every file must have one new stable scorer ID. Before opening the private map or allocating a report path, check the locked plan and completed targeted files:

```powershell
python evals/harness.py adjudication-check `
  --public-bundle C:\absolute\scoring-bundle `
  --plan C:\absolute\scores\adjudication-plan.json `
  --initial-scores C:\absolute\scores\initial-a.jsonl `
  --initial-scores C:\absolute\scores\initial-b.jsonl `
  --adjudicator-scores C:\absolute\scores\targeted-adjudicator.jsonl
```

Repeat `--adjudicator-scores` only for additional files that cover disjoint targets. The check regenerates the plan from the immutable bundle and exact two initial files, compares it with the locked plan, validates every targeted record and exact coverage, prints a treatment-blind result, and exits nonzero on any error. It does not accept or read the private map and writes no report. Aggregate once after the check passes and all required files are final:

The plan and final report use exclusive output creation and must be outside the repository, frozen run (including trial subdirectories), and immutable public bundle. Existing or link/reparse output nodes and resolved protected-tree overlaps are refused without mutation.

```powershell
python evals/harness.py score `
  --public-bundle C:\absolute\scoring-bundle `
  --private-map C:\absolute\allocator-only\blind-map.json `
  --initial-scores C:\absolute\scores\initial-a.jsonl `
  --initial-scores C:\absolute\scores\initial-b.jsonl `
  --adjudicator-scores C:\absolute\scores\targeted-adjudicator.jsonl `
  --adjudication-plan C:\absolute\scores\adjudication-plan.json `
  --seed 20260830 `
  --out C:\absolute\results\report.json
```

When targets exist, final scoring requires the same canonical version-2 plan bytes that passed `adjudication-check`; it compares both decoded structure and exact bytes against a plan regenerated from the immutable public bundle and exact two initial files. Omit `adjudication-check`, `score --adjudicator-scores`, and `score --adjudication-plan` when there are no targets; supplying a plan without targets is invalid. The score output path must not already exist. Here `score --seed` is the bootstrap seed; the harness performs exactly 10,000 hierarchical-bootstrap draws. `score` deliberately preserves its existing audit behavior: a missing, incompatible, reformatted, or mismatched plan and well-formed scorer records that fail contract/coverage validation produce a saved protocol-invalid report with explicit errors rather than erasing the failed attempt. Missing, unreadable, malformed-JSON, or invalid-UTF-8 scorer inputs abort before a report is created. The separate check is the fail-fast gate before allocating that final path.

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

An interrupted, ineligible run may contain `operator-abort.json` and, after an unproven cleanup, `operator-abort-cleanup.json` instead of `operator-summary.json`; an uncatchable host shutdown may leave only `operator-config.json`. None of these layouts can be blinded. See the treatment-blind disclosure for [aborted confirmatory attempt v2](evidence/ABORTED-CONFIRMATORY-V2.md).

The public scoring bundle has exactly four root files—`bundle.json`, `RUBRIC.md`, `scorer.schema.json`, and the immutable `score-template.jsonl`—plus exactly one `packets/<blind-id>.json` regular file for every listed blind ID; no other file or directory is accepted. When output is present, its blind packet carries the exact raw response bytes as Base64 plus SHA-256 and byte count, alongside the parsed response or parse errors; missing output is represented explicitly. The allocator-only map binds blind IDs back to trials; retains the HMAC key and both keyed orders; and embeds and hashes the operator config and summary, the exact complete operator-chain manifest, and the allocation, raw-response, packet, and complete public-bundle hashes.

`adjudication-plan.json` binds the public-bundle manifest and both initial scorer manifests to the treatment-blind target templates. `report.json` records adjudication contract version 2.0 and attests whether a plan was required/provided, its SHA-256, byte count, target count, schema version, and contract version. It also records all seeds, the blind-key commitment (never the key), the fixed scoring configuration, sanitized operator configuration and outcomes, fixture/treatment hashes, allocation and private-map hashes, the exact operator-chain and public-bundle manifests, raw-response and blind-packet hashes, deterministic adjudication targets, and each scorer file's role, stable scorer ID, covered blind IDs, hash, and byte count. It reports initial-pair exact agreement and agreement within one point, and includes the bootstrap seed and iteration count. Plan and scorer bytes are rechecked before the report is returned.

## Acceptance logic

The deterministic scorer checks the support label, eligible counts and IDs, counterevidence, WTP, citations/provenance, untrusted-source canaries, privacy, and whether the recommendation is allowed by the case oracle. A disallowed recommendation and fabricated public or private provenance are automatic critical failures; they cannot pass even if the rubric scores are high.

The absolute skill floor includes mean rubric scores of at least 3/4 for `independence_counting`, `promotion_handling`, `counterevidence`, `wtp_discipline`, `provenance_privacy`, `evidence_ceiling`, **and `decision_quality`**. See the protocol for every gate.

Critical adjudication is based on occurrence, not matching codes. Initial occurrence disagreement deterministically targets that response for one third vote; the resulting three-person strict majority decides occurrence. When the initial scorers agree, the adjudicator must leave `critical_failures` empty and no third vote is used. When occurrence reaches a majority, all reported codes are retained and code disagreement remains visible. For ratings, the adjudicator supplies values only for dimensions with an initial gap of at least 2; every other rating is `null`, and undisputed dimensions retain the mean of the two initial ratings. Reliability is computed only from the initial pair. The shared scorer schema exposes the structural integer-or-null union; the dependency-free harness enforces the stricter packet- and plan-dependent role rules.

## Content-addressed chain

`verify` hashes every fixture, oracle, schema, rubric, and treatment resource. `prepare` freezes those hashes and an exact path/hash manifest for every trial. The operator stages only that manifest and checks it before launch. Every allocated trial is a strict direct child identified by the canonical `trial-<16 lowercase hex>` grammar. `blind` requires and validates the full operator config/summary/execution/log/response chain, preserves raw bytes, rejects link/junction/reparse traversal, generates keyed 128-bit IDs and two domain-separated orders, and hashes every blind artifact. `score` re-verifies the live suite, allocation, complete operator chain, HMAC key commitment and derived identities/orders, trial mapping, raw bytes, packets, exact public-bundle tree, and scorer role/identity/coverage bindings before and after aggregation.

Consequently, changing a frozen resource or scoring artifact is an error, not an implicit new run. Preserve the original run directory, public bundle, private map, scorer files, verification output, and final report together.

The public-bundle integrity boundary covers exact relative paths, each regular file's base/unnamed data-stream bytes, the exact file and directory sets, and normalized modification times. Filesystem creation/access times, attributes, ownership, ACLs, physical layout, extended attributes, resource forks, and Windows alternate data streams are not enumerated evidence fields. Deliver scorers a trusted clean export/archive containing only the allowlisted paths, or a clean extraction made from it; do not expose the allocator's original writable filesystem tree. Scorers must use only the listed file contents and must not inspect filesystem metadata, attributes, extended data, or alternate streams for treatment clues. This transport rule is procedural—the harness cannot prove that a filesystem lacks every out-of-band channel. The canonical honest allocator removes condition-correlated creation order through the keyed emission permutation; a malicious allocator or distributor capable of fabricating or leaking the run is outside the protocol's trust boundary.

No behavioral result is established merely because this harness exists. A defensible result requires the preregistered configuration, complete first-attempt records (including missing, failed, or timed-out outcomes), independent blind scores, and passing integrity gates in `report.json`.
