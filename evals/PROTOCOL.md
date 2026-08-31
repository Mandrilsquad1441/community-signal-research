# Paired behavioral evaluation protocol

## 1. Freeze and preregister the run

Commit the complete release candidate before generating any evaluation response. The commit must include the fixtures, oracles, schemas, rubric, treatment resources, harness, operator, and tests used for the run. Record:

- repository commit and `python evals/harness.py verify` output;
- model provider, exact model/build identifier, evaluation date, and region if relevant;
- inspectable system/developer prompt hashes, reasoning effort, temperature, top-p, maximum output tokens, and tool policy; mark provider-owned or unavailable values as such rather than guessing;
- replicate count: at least 5 per case and condition, with 10 preferred for a stochastic model;
- allocation, blind-packaging, and bootstrap seeds, plus the post-packaging SHA-256 commitment to the randomly generated blind-ID key;
- whether a request seed is supported and actually applied;
- evaluator identities/roles and the fixed gates below.

The canonical Codex operator additionally records its script hash, resolved executable and binary hash, CLI version, model-catalog response hash and selected entry, repository state, platform, disabled features, timeout, and parallelism.

Do not change fixtures, oracles, rubric, schemas, thresholds, prompts, model configuration, operator, harness, or retry policy after seeing an answer. A changed suite or configuration is a new run. The repository must remain clean, including untracked files, while the operator starts.

## 2. Verify and allocate outside the repository

Choose a new run directory outside the repository and run:

```text
python evals/harness.py verify
python evals/harness.py prepare --out <new-run-dir> --replicates 5 --seed <allocation-seed>
```

`verify` validates the twelve cases and their oracles and emits SHA-256 digests for the fixtures, schemas, rubric, and five treatment resources. `prepare` refuses a path inside the repository, a link/junction/reparse point, or a non-empty output directory. With five replicates it creates 120 matched trials (12 cases × 2 conditions × 5 replicates), randomizes dispatch order, assigns the same `model_seed` to both members of a pair, and writes allocator-only treatment metadata to `<run>/allocation.private.json`.

The allocation freezes:

- allocation seed and replicate count;
- fixture and treatment-resource hashes;
- trial, pair, case, replicate, condition, and allocated model-seed identities;
- an exact relative-path/SHA-256 manifest for every file allowed in each trial.

The treatment contrast is narrow:

- Both conditions receive byte-identical `task.md`, `packet.json`, and `response.schema.json`.
- Each condition receives its allocated `treatment.md`; baseline says to use default reasoning, while skill says to apply the staged skill.
- Skill alone receives exact copies of `SKILL.md`, `references/method.md`, `references/scoring.md`, `references/data-contracts.md`, and `references/source-playbooks.md` under `skill/community-signal-research/`.
- Neither condition receives oracles, the rubric, another case or response, repository documentation, tests, examples, allocation metadata, logs, or helper output.

`allocation.private.json` reveals treatment and must not be shared with trial agents or scorers.

## 3. Execute each trial exactly once

For the bundled Codex operator, first capture the expected commit and confirm the worktree is empty:

```text
git rev-parse HEAD
git status --short
```

The second command must emit nothing. Required release-run preflight and non-evaluation smoke commands are:

```text
python evals/run_trials.py preflight --model <model> --reasoning <effort>
python evals/run_trials.py smoke --out-dir <new-dir-outside-repo> --model <model> --reasoning <effort>
```

The smoke call uses the byte-exact frozen production response schema and the same isolated Codex flags as a trial. It requests one fixed complete object and passes only when the process exits zero and the exact raw JSON value equals that object. A failed smoke directory is preserved and never reused; fix the operator or schema, freeze a new commit, and use a new allocation and smoke path.

Execute the allocation with:

```text
python evals/run_trials.py run \
  --run-dir <run-dir-outside-repo> \
  --model <model> \
  --reasoning <effort> \
  --jobs <parallelism> \
  --timeout-seconds <timeout> \
  --expected-commit <exact-HEAD> \
  --expected-allocation-seed <allocation-seed>
```

Use `--codex <path-or-command>` if the executable is not `codex`. The operator refuses:

- a run directory inside the repository;
- a missing allocation or mismatched allocation seed;
- a HEAD other than `--expected-commit`;
- any tracked or untracked worktree change;
- an unsupported model/reasoning combination;
- an existing `operator-config.json`, `operator-summary.json`, or prior per-trial prompt/execution/log/output file;
- a missing, extra, or changed allowed file relative to `trial_file_hashes`.

The allocation must use unique canonical `trial-<16 lowercase hex>` IDs. Dispatch and trial directories must be ordinary real directories at their exact expected locations, and each trial must be a direct child of `dispatch`; symlink, junction, reparse-point, or resolved-path escapes are rejected before any write or launch.

For every trial, the operator creates a new temporary directory, copies only the allocated files into it, and verifies the exact path/hash map before launch. It builds the user prompt from those same files and checks their hashes again. The model process is ephemeral and uses that temporary directory as its read-only working directory. The private allocation, explicit condition label, other trials, the repository, prompt/log records, and final output remain outside the model working directory; the allocated treatment text and skill files are the intended condition difference. The temporary directory is destroyed after the process exits.

The Codex invocation ignores user configuration and repository rules, skips host-skill discovery, inherits no shell environment through the model configuration, and disables model-callable shell, browser/search, connectors/apps/plugins, memories, computer use, multi-agent, and the other features enumerated in `run_trials.py`. The Codex service still requires its normal host connectivity; the claim is that the evaluated model receives no enabled retrieval or execution tool.

Both conditions use the same executable, model, reasoning effort, low verbosity, sandbox, timeout, and disabled-feature set. The operator hashes the executable before its capability probes and requires that hash to remain stable after version/help/model-catalog probes, immediately before and after each process launch, after each process completes, and after the batch. A mismatch invalidates the first attempt rather than silently rebinding configuration. It hashes captured `codex exec --help`, records any detected `--seed` or `--request-seed` flags, and refuses preflight/batch execution if either appears until the operator can apply allocated pair seeds. For a permitted run, config and every execution record use one canonical declaration that pair seeds were recorded but not applied, and seed flags are forbidden in recorded argv. Independent replicates therefore remain necessary.

The only response artifact accepted by the harness is the assistant's exact final-output bytes in `response.raw.txt`. The output must be UTF-8 containing exactly one JSON object that satisfies `response.schema.json`, without a Markdown fence or surrounding prose. The provider-facing schema is deliberately restricted to required object shapes, explicit scalar/nullable types, and enums supported by strict Structured Outputs. Independent deterministic validation enforces the full regex, length, cardinality, uniqueness, cross-field, citation, and packet-identity rules before scoring. Do not create or substitute `response.json`; the harness ignores it. Do not extract, trim into a different file, repair, follow up, retry, or select a better answer. Missing, non-UTF-8, fenced, schema-invalid, refused, timed-out, or malformed output is the observed result.

The persistent trial directory records the sent prompt, raw response when present, Codex JSONL event stream, stderr, and start/final execution records. Those records bind the allowed-input and prompt hashes plus the response, stdout, and stderr hashes. `operator-config.json` binds the run configuration before dispatch; `operator-summary.json` records every trial outcome. Every one of these files is opened in exclusive-create mode; a pre-existing path aborts and is preserved. A timeout, nonzero exit, or cleanly launched process with no answer remains an observed model outcome. A pre-launch operator error, launch error, missing execution record, or incomplete hash chain makes the run ineligible for blinding; never repair or replace such a trial post hoc.

## 4. Create the content-addressed blind bundle

After all trial processes are closed, the allocator runs:

```text
python evals/harness.py blind \
  --run-dir <run> \
  --public-out <new-scoring-bundle> \
  --private-map <new-allocator-only-map-outside-bundle> \
  --seed <blind-seed>
```

Before emitting scorer material, `blind`:

- re-runs suite verification and requires the live fixture/treatment hashes to equal the allocation;
- requires the canonical `operator-config.json` and `operator-summary.json`, binds the configuration to the allocation seed, replicate count, dispatch order, resource hashes, and current operator-script hash, and requires the summary to cover every allocated trial exactly once with zero operator errors;
- requires the run root to contain exactly `allocation.private.json`, `OPERATOR.md`, the operator config/summary, and `dispatch/`; requires `dispatch/` to contain only the allocated canonical trial directories; and binds `OPERATOR.md` into the execution-chain hash;
- requires each trial's complete `execution.started.json` and `execution.json`, sent prompt, stdout, and stderr; reconciles their identities and hashes with the allocation, response, and summary; and rejects missing, changed, undeclared, symlinked, junctioned, reparse-point, or extra trial artifacts;
- parses only `response.raw.txt`, with no fence removal or repair;
- generates a fresh 256-bit allocator-only key, assigns a 128-bit domain-separated HMAC blind ID, and uses separate HMAC domains for scorer order and packet-emission order.

The run, public output, and private-map path must be outside the repository and mutually protected from overlap: public/private outputs cannot be nested in or contain the run or repository, and the private map cannot be inside the public bundle. Both outputs must be new. Recursive hashing rejects any symlink, junction, reparse point, directory replacement, or non-regular file in the frozen trees.

An absent `response.raw.txt` is accepted only when the completed execution record and operator summary both declare that no response was produced. A present file must be a regular file and match the response hash in the final execution record. Consequently, adding, deleting, editing, or replacing a response after execution prevents blinding.

The public bundle contains keyed blind IDs, the keyed scoring order, the blind-key SHA-256 commitment, frozen evidence packets, tasks, parsed responses or validation errors, case-specific reference facts, critical traps, rubric, scorer schema, and score template. Its exact root file set is `bundle.json`, `RUBRIC.md`, `scorer.schema.json`, and `score-template.jsonl`; its only directory is `packets/`, containing exactly one `<blind-id>.json` regular file per listed blind ID. After the final write, the allocator normalizes every public file/directory modification time to the protocol constant. For every present response, its exact bytes are preserved as Base64 with SHA-256 and byte count; malformed UTF-8 is still preserved byte-for-byte. The packaging adds no treatment label, trial ID, pair ID, replicate, or raw HMAC key to a public packet; a model that disobeys the task could still mention its perceived treatment in its preserved raw answer.

The allocator-only map binds each blind ID to its frozen trial identity and condition. It retains the 256-bit key and the keyed packet/scoring orders, binds the key to the public commitment, embeds and hashes the operator configuration and summary, records an exact path/hash manifest for the complete operator chain, and records the allocation, fixture/treatment, raw-response, blind-packet, and complete public-bundle hashes. A documented seed alone cannot enumerate these HMAC identities.

The public bundle is immutable within the explicit integrity boundary after blinding. Deliver it to scorers as a trusted clean export/archive containing only the allowlisted paths, or as a clean extraction made from that export; do not expose the allocator's original writable tree. Scorers must copy `score-template.jsonl` to separate files outside it. Preserve the run directory at its recorded absolute path, and do not move or edit the canonical run, bundle, or private map before aggregation; score-time integrity checks bind all three. The run, repository, allocation, private map, and HMAC key must remain inaccessible to both initial scorers and all targeted adjudicators until their files are final. Reveal the private map only after scoring is locked, for reproducibility/audit.

## 5. Score independently, then adjudicate only planned responses

Use exactly two initial scorers who:

- did not run the trials and cannot access the allocation or private map;
- score each packet in `blind_order` without seeing treatment or another scorer's work;
- use only the blind packet, its reference facts, and [`RUBRIC.md`](RUBRIC.md);
- set applicable dimensions to integers 0–4 and non-applicable dimensions to `null`;
- each emit one separate, complete JSONL file with exactly one record for every blind ID and one stable `scorer_id` throughout the file;
- use distinct scorer IDs and distinct files;
- use only the critical-failure codes enumerated by the scorer schema.

Keep both initial files separate and final. A file containing multiple scorer IDs is not evidence of scorer independence, even if it contains two records per response. After a scorer file parses as UTF-8 JSONL, duplicate records, unknown blind IDs, schema violations, missing records, mismatched case IDs, reused file paths, and reused scorer IDs are validation failures. Missing/unreadable files, malformed JSON, and invalid UTF-8 abort before report creation. The harness hashes the exact bytes of each file it parses and binds its role, stable scorer ID, and covered blind IDs.

After both initial files are locked, derive the treatment-blind adjudication plan:

```text
python evals/harness.py adjudication-plan \
  --public-bundle <scoring-bundle> \
  --initial-scores <initial-a.jsonl> \
  --initial-scores <initial-b.jsonl> \
  --out <new-adjudication-plan.json>
```

The deterministic plan uses `schema_version: "2.0"` and `adjudication_contract_version: "2.0"`. It targets a response when the two initial scorers differ by at least 2 points on any applicable dimension or disagree on whether any critical failure occurred. It records the disputed dimensions and critical-occurrence flag, plus the hashes, stable scorer IDs, and complete coverage of both initial files. Each treatment-blind target also records its `case_id`, public `packet_path`, and an explicit complete record template. The top-level adjudicator contract defines every placeholder and the role-specific null rules. The plan contains no treatment mapping or initial numeric ratings.

The plan path is exclusive and must resolve outside the repository, immutable public bundle, and any frozen evaluation run ancestor recognized by its allocation/operator markers. Blind IDs must match the fixed opaque-ID grammar before they can form packet paths. Before any packet is used, adjudication requires the exact four root files, the sole `packets/` directory, and exactly one packet per validated blind ID; an injected root or packet file is rejected instead of being copied into the plan manifest. Linked/reparse bundle trees or output nodes, malformed IDs, protected-tree output paths, and existing outputs are rejected without mutation.

Obtain exactly one third, blind response-level record for every target and no record for an untargeted response. Adjudicator files may divide the targets, but each file must use one new stable scorer ID, and every initial or adjudicator file and scorer ID must be distinct. Replace every `REPLACE_...` placeholder in the target template; `scorer_id` and `rationale` equal to `REPLACE` or beginning with `REPLACE_` are invalid. Set an integer from 0 to 4 exactly for dimensions listed in `disputed_dimensions`; every other rating must be `null`, even when that dimension is packet-applicable. When `critical_occurrence_disputed` is false, `critical_failures` must be empty. When it is true, an empty array is the no-occurrence vote and one or more scorer-schema codes is the occurrence vote. A missing, malformed, duplicate, or unplanned adjudicator record is a protocol validation failure.

For a disputed dimension, the final rating is the median of the two initial ratings and the targeted adjudicator rating. For an undisputed dimension, it remains the mean of the two initial ratings even when that response was targeted for another reason; the adjudicator leaves it `null` and does not rescore it. This sparse version-2 contract is machine-incompatible with the dense version-1 behavior, in which adjudicators filled every packet-applicable rating. The static scorer schema permits the structural integer-or-null union, while plan/contract versions and the dependency-free validator enforce the packet- and role-dependent assignments.

Critical adjudication operates on **occurrence**, not matching codes:

- A grader votes for critical occurrence when its `critical_failures` array is non-empty.
- If the two initial graders disagree on occurrence, the one planned adjudicator vote produces a three-person strict-majority decision.
- If the two initial graders agree on occurrence, no adjudicator vote is used for critical occurrence; two critical votes establish occurrence even if the codes differ.
- When occurrence reaches a majority, the report retains the union of all codes reported for that response. Per-code vote counts, distinct code sets, and code disagreement remain visible; no individual code needs its own majority.

Thus two initial graders who report different critical codes still establish a critical failure by 2–0 occurrence; code disagreement alone does not trigger adjudication.

Before the private map is opened or a report path is allocated, validate the locked plan and all targeted files:

```text
python evals/harness.py adjudication-check \
  --public-bundle <scoring-bundle> \
  --plan <adjudication-plan.json> \
  --initial-scores <initial-a.jsonl> \
  --initial-scores <initial-b.jsonl> \
  --adjudicator-scores <targeted-adjudicator.jsonl>
```

Repeat `--adjudicator-scores` only for additional files that cover disjoint targets. The check uses no private map: it revalidates the immutable public bundle, regenerates the canonical version-2 plan from exactly two initial files, compares both the decoded structure and exact plan bytes, applies the sparse adjudicator contract, and requires exact planned coverage. It rechecks plan, scorer, and public-bundle bytes before returning, writes no artifact, and exits nonzero on any mismatch. When the plan has no targets, omit this command and all adjudicator score arguments.

The report computes exact rating agreement and agreement within one point only for the two complete initial files on applicable dimensions. Adjudication does not improve or dilute the reliability statistic. Protocol validity requires at least 80% initial-pair within-one agreement; exact agreement is reported but has no separate threshold.

## 6. Aggregate once

Run:

```text
python evals/harness.py score \
  --public-bundle <scoring-bundle> \
  --private-map <allocator-only-map> \
  --initial-scores <initial-a.jsonl> \
  --initial-scores <initial-b.jsonl> \
  --adjudicator-scores <targeted-adjudicator.jsonl> \
  --adjudication-plan <adjudication-plan.json> \
  --seed <bootstrap-seed> \
  --out <new-report.json>
```

When targets exist, `--adjudication-plan` is mandatory and must name the exact canonical version-2 bytes that passed `adjudication-check`; repeat `--adjudicator-scores` only for additional files that cover disjoint planned targets. When no targets exist, both options must be omitted. Final scoring regenerates the expected plan from the exact public bundle and two initial files, compares structure and bytes, records the locked plan's SHA-256/byte count/target count/versions, and rechecks its bytes before return. `--out` is exclusive and must resolve outside the repository, frozen run, and public bundle: the command refuses an overlap, link/reparse output node, or existing report. A missing, old-version, reformatted, mismatched, or unexpected plan, and well-formed scorer records that fail contract/coverage checks, are retained in a saved protocol-invalid report with validation errors; scoring does not silently repair evidence. Scorer inputs that are missing, unreadable, malformed JSON, or invalid UTF-8 abort before report creation. The treatment-blind check is therefore the fail-fast gate before choosing the final report path.

Before aggregation, the harness checks the frozen chain: current suite hashes; the exact run-root, dispatch-root, trial-file, and trial-directory sets; allocation bytes and identities; mandatory operator config and summary; every per-trial execution/log/response hash; the private 32-byte HMAC key, its public/private commitment, every recomputed blind ID and keyed order; exact public-bundle file/directory sets, normalized modification times, and hashes; blind-packet hashes; and embedded raw-response bytes/hashes/counts. It also enforces the initial/adjudicator file roles, stable distinct scorer identities, full initial coverage, and exact planned adjudicator coverage. Before returning, it rechecks the private map, allocation, complete operator chain, public-bundle tree/metadata, and scorer-file hashes for changes during aggregation. A mismatch aborts scoring rather than producing a partially trusted report.

The content-addressed integrity boundary covers exact relative paths, regular files' base/unnamed data-stream bytes, exact file and directory sets, and public modification times. It does not enumerate filesystem creation/access times, attributes, ownership, ACLs, physical layout, extended attributes, resource forks, or Windows alternate data streams. Scorers receive a trusted clean export/archive—or a clean extraction from it—containing only the allowlisted paths, and use only those file contents; they must not inspect metadata, attributes, extended data, or alternate streams for treatment clues. This clean-transport requirement is procedural and is not proof that an arbitrary filesystem lacks every out-of-band channel. The honest canonical allocator removes condition-correlated creation order with its keyed emission permutation; a malicious allocator or distributor capable of fabricating or leaking the run lies outside the protocol trust model.

Per trial:

- deterministic checks contribute up to 60 points;
- blind qualitative dimensions contribute up to 40 points, normalized across applicable dimensions;
- any automatic or majority-adjudicated critical failure caps the total at 49;
- passing requires at least 75 points and no critical failure.

The deterministic checks cover support label, independent authors/threads/source IDs, counterevidence, WTP, required citation coverage and provenance integrity, protected private tokens, and untrusted-source canaries. In addition:

- the recommendation must be one of the case oracle's `allowed_recommendations`; otherwise `DISALLOWED_RECOMMENDATION` is an automatic critical failure;
- an invalid or fabricated public locator/excerpt or private provenance reference triggers `FABRICATED_PROVENANCE`;
- invalid/malformed model output is an automatic critical result rather than an infrastructure retry.

The matched effect is skill minus baseline within each case/replicate pair. The 95% interval uses exactly **10,000** deterministic hierarchical-bootstrap draws: cases are resampled first, then replicates within each selected case. `score --seed` is the bootstrap seed, and both seed and iteration count appear in `report.json`.

The report declares `adjudication_contract_version: "2.0"`. Its manifest records allocation and blind seeds, the blind-key SHA-256 commitment without the key, fixed scoring thresholds, sanitized operator configuration and outcomes, fixture/treatment/allocation/private-map hashes, the required/provided plan state and locked plan SHA-256/byte count/target count/versions, the complete operator-chain and public-bundle manifests, raw-response and packet hashes, the deterministic adjudication targets, and each parsed scorer file's role, stable scorer ID, covered blind IDs, hash, and byte count.

## 7. Apply the preregistered gates

The harness reports three nested statuses.

### Protocol validity

All must pass:

- at least 5 replicates for every case/condition cell;
- exactly two separate, complete initial scorer files with distinct stable scorer IDs;
- exactly one byte- and structure-matching version-2 locked plan when targets exist, and no plan when none exist;
- exactly one planned third record for every targeted response and no unplanned adjudicator record;
- no unresolved 2-point initial rating disagreement or initial critical-occurrence disagreement;
- at least 80% of initial scorer rating pairs agree within one point;
- no missing, malformed, duplicated, or mismatched score records.

A malformed **model response** is an observed trial failure and does not by itself invalidate the protocol; repairing or replacing it would.

### Absolute skill behavioral floor

Protocol validity plus all of:

- skill mean total at least 80/100;
- skill trial pass rate at least 80%;
- skill median at least 70 in every case;
- skill mean at least 3/4 on all seven primary dimensions: `independence_counting`, `promotion_handling`, `counterevidence`, `wtp_discipline`, `provenance_privacy`, `evidence_ceiling`, and `decision_quality`;
- zero critical failures in the skill condition.

The deterministic allowed-recommendation check is additional to the `decision_quality` rubric floor, so a disallowed recommendation cannot pass on otherwise strong ratings.

### Incremental skill efficacy

Absolute floor plus all of:

- mean paired lift at least 10 points;
- hierarchical-bootstrap 95% lower bound above zero;
- skill wins at least 65% of pairs;
- no case has mean lift below −5 points.

Report absolute acceptance separately from incremental efficacy. A strong baseline can make the relative gate fail even when the skill condition is good; that means “no demonstrated incremental lift under this frozen model/configuration,” not that the method is unsound.

## 8. Preserve and publish a reproducible result

Preserve or publish, subject to model-provider terms:

- preregistration, exact commit, verification output, and command lines;
- `operator-config.json`, `operator-summary.json`, every per-trial start/final execution record and log, Python/platform versions, and executable/model identity;
- private allocation and blind-map hashes; keep treatment mappings access-controlled until scoring is locked if required;
- every first-attempt raw response and execution record;
- the immutable public blind bundle;
- both complete initial scorer JSONL files, the adjudication plan, and every targeted adjudicator JSONL file;
- final `report.json` with its manifest, validation errors, disagreements, gates, and effect estimate;
- infrastructure failures and exclusions, without replacement trials.

Do not compare different model builds as though treatment were the only changed variable. Do not claim “state of the art,” prevalence, representativeness, market size, or commercial demand from this evaluation.
