# Paired behavioral evaluation protocol

## 1. Preregister the run

Before any response is generated, record:

- repository commit or archive hash;
- output of `python evals/harness.py verify`;
- model provider, exact model/build identifier, evaluation date, and region if relevant;
- system/developer prompt hashes, reasoning-effort setting, temperature, top-p, maximum output tokens, and tool policy;
- number of replicates (minimum 5 per case per condition; 10 is preferred for stochastic models);
- allocation seed, blind-packaging seed, bootstrap seed, and whether the runtime honors per-request model seeds;
- evaluator identities/roles and the fixed pass gates below.

Do not change fixtures, rubric, thresholds, prompts, model configuration, or retry policy after inspecting an answer. A changed suite is a new version and a new run.

## 2. Verify and allocate

Run:

```text
python evals/harness.py verify
python evals/harness.py prepare --out <new-run-dir> --replicates 5 --seed <allocation-seed>
```

`prepare` creates paired baseline and skill trials, randomizes dispatch order, assigns the same `model_seed` to each pair, and records treatment in `<new-run-dir>/allocation.private.json`. That file is allocator-only.

The treatment is intentionally narrow:

- Both conditions receive byte-identical `task.md`, `packet.json`, and `response.schema.json`.
- Baseline receives only a neutral instruction to use default reasoning.
- Skill receives the instruction to use the staged skill plus exact copies of `SKILL.md`, `references/method.md`, `references/scoring.md`, `references/data-contracts.md`, and `references/source-playbooks.md`.
- Neither condition receives the oracles, rubric, another case, another response, repository README, tests, examples, or helper output.

## 3. Execute independent trials

Each trial must run in a fresh process/session with no prior messages, persistent memory, retrieval corpus, shared scratchpad, or conversation fork. Use the same model configuration and system/developer messages in both conditions. The treatment file and staged skill resources are the only condition difference.

Isolation requirements:

1. Copy one dispatch directory into a disposable container, VM, projectless workspace, or upload-only chat. Do not mount the source checkout, `evals/oracles`, allocation file, or other trial directories.
2. Disable network access, web search, connectors, and tools that can reach sources outside the trial directory. File reads inside the isolated directory are allowed.
3. If the runtime auto-discovers skills, configure an empty skill home for baseline and a skill home containing only the staged treatment for skill. Disable unrelated global/project skills in both.
4. Supply `task.md` and `treatment.md` as the user task and attach `packet.json` plus `response.schema.json`. Do not add case-specific hints.
5. If the runtime accepts a seed, use the pair's `model_seed` from the private allocation. If it does not, record that fact and retain all independent replicates.
6. Save the assistant's unedited final answer as `response.raw.txt`. Do not repair JSON, ask a follow-up, retry a refusal, or cherry-pick a better sample. A malformed or missing answer is an observed failure.
7. Destroy the agent session before the next trial. Randomized `dispatch_order` prevents a systematic condition-order effect.

Prompt instructions alone are not sufficient isolation. An unrestricted agent located in the source checkout could read the oracle and invalidate the comparison.

## 4. Blind the responses

After every trial is closed, the allocator runs:

```text
python evals/harness.py blind --run-dir <run> --public-out <scoring-bundle> --private-map <allocator-only-map> --seed <blind-seed>
```

The public bundle contains shuffled opaque response IDs, the frozen packet, parse status, case-specific reference facts, applicable rubric dimensions, and no treatment label. The private map must live outside the shared scoring directory. Preserve raw trial directories; the blind command never alters them.

## 5. Score independently

Use at least two scorers who:

- have not run the agents and cannot access the allocation/private map;
- score each packet in the emitted blind order without seeing another scorer's work;
- use only the packet, reference facts, response, and [`RUBRIC.md`](RUBRIC.md);
- set non-applicable dimensions to `null` and applicable dimensions to integers 0–4;
- flag only the enumerated critical-failure codes.

The deterministic scorer separately checks support label, eligible counts, canonical support IDs, counterevidence, WTP, and citation/provenance integrity. It does not infer prose quality.

If two graders differ by 2 or more points on a dimension, or disagree on whether a critical failure occurred, obtain a third blind adjudication before publication. The harness treats an unadjudicated disagreement as an integrity-gate failure. Report exact agreement and agreement-within-one rates.

## 6. Aggregate once

Run `harness.py score` with all final scorer files. Per trial:

- deterministic hard checks: 60 points;
- blind qualitative rubric: 40 points, normalized across the case's applicable dimensions;
- any automatic or adjudicated critical failure caps the total at 49;
- trial pass: at least 75 points and no critical failure.

The effect estimate is the skill-minus-baseline score within each matched case/replicate pair. The 95% interval is a deterministic hierarchical bootstrap that resamples cases and then replicates within cases (10,000 draws). This protects against treating many runs of one case as independent case coverage.

## 7. Preregistered gates

The harness reports three nested statuses.

### Protocol validity

All must pass:

- at least 5 replicates for every case/condition cell;
- at least 2 independent scorer records per response;
- no unresolved 2-point rating disagreement or critical-failure disagreement;
- at least 80% of scorer pairs agree within one point;
- no missing, malformed, duplicated, or mismatched score records.

### Absolute skill behavioral floor

Protocol validity plus all of:

- skill mean total at least 80/100;
- skill trial pass rate at least 80%;
- skill median at least 70 in every case;
- mean at least 3/4 on independence, promotion, counterevidence, WTP, private provenance, and evidence-ceiling dimensions;
- zero critical failures in the skill condition.

### Incremental skill efficacy

Absolute floor plus all of:

- mean paired lift at least 10 points;
- hierarchical-bootstrap 95% lower bound above zero;
- skill wins at least 65% of pairs;
- no case has mean lift below −5 points.

Report absolute acceptance separately from incremental efficacy. A strong baseline can make the relative gate fail even when the skill condition is good; that result means “no demonstrated incremental lift under this model/configuration,” not that the method is unsound.

## 8. Publish a reproducible result

Preserve or publish, subject to model-provider terms:

- preregistration and configuration record;
- verification output and commit/archive hash;
- private allocation hash (the mapping itself may remain access-controlled until scoring is locked);
- all raw responses, public blind bundle, scorer JSONL, adjudications, and final `report.json`;
- exclusions or infrastructure failures, with no post-hoc replacement trials;
- exact harness command lines and Python version.

Do not compare runs across different model builds as though condition were the only causal variable. Do not publish “state of the art,” prevalence, or market claims from this evaluation.
