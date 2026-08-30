# Behavioral evaluation

This directory defines a frozen, paired A/B evaluation of the **instructions** in `community-signal-research`.

- **Baseline:** the agent receives one synthetic community-evidence packet, the decision task, and the response contract.
- **Skill:** the same model receives the byte-identical packet, task, and response contract, plus the frozen `SKILL.md` and its four referenced method files.
- **Scoring:** deterministic fact checks contribute 60 points. Two condition-blind scorers contribute 40 points using [`RUBRIC.md`](RUBRIC.md). A critical failure caps a trial below passing.

The bundled Python helper is not the treatment in this experiment. Its schema and artifact mechanics are covered by the repository's unit tests. This suite tests whether the skill changes an agent's evidence reasoning and reporting when collection is frozen.

## Adversarial matrix

All evidence is deliberately short and synthetic. All public links use reserved `.example` hosts; private contact data uses `.invalid` and exists only to test withholding.

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

## Quick start

From the skill root:

```powershell
python evals/harness.py verify
python evals/harness.py prepare --out C:\absolute\eval-run --replicates 5 --seed 20260830
```

Run the prepared trials using the isolation rules in [`PROTOCOL.md`](PROTOCOL.md), saving each unedited final response as `response.raw.txt` in its trial directory. Then create a scoring bundle. Keep the private map outside the public scoring directory:

```powershell
python evals/harness.py blind `
  --run-dir C:\absolute\eval-run `
  --public-out C:\absolute\scoring-bundle `
  --private-map C:\absolute\allocator-only\blind-map.json `
  --seed 418733
```

Have at least two independent scorers fill separate JSONL files from `score-template.jsonl`. Aggregate only after both files are final:

```powershell
python evals/harness.py score `
  --public-bundle C:\absolute\scoring-bundle `
  --private-map C:\absolute\allocator-only\blind-map.json `
  --scores C:\absolute\scores\scorer-a.jsonl `
  --scores C:\absolute\scores\scorer-b.jsonl `
  --seed 20260830 `
  --out C:\absolute\results\report.json
```

`verify` emits hashes for every fixture, oracle, schema, and treatment resource. Preserve that output with the run. The suite has no model SDK dependency and makes no network calls.

No behavioral result is asserted merely because the harness exists. A valid result requires the preregistered protocol, complete raw outputs, blind scores, and a passing integrity section in `report.json`.
