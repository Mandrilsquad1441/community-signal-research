# Evidence labels and ranking

The tool keeps three concepts separate:

- **Demand evidence** describes what eligible, deduplicated records in this sample say or demonstrate.
- **Counterevidence status** describes whether disconfirming searches were run and what they found.
- **Coverage-execution score** describes how completely the declared research plan was executed.

None is statistical confidence, population prevalence, market size, revenue potential, or proof that a remote account represents one unique person.

## Independence and eligibility

Hard duplicate groups are formed from explicit `repost_of` links, `same_source` reviews, equal `(platform, unit_id)` pairs, equal canonical unit URLs, or exact substantive normalized captured text. The origin is an explicit repost target when present; otherwise it is the earliest publication timestamp, then lexical source ID. The origin contributes at most one author key, thread, community, platform, and recency unit.

Five-token shingle similarity over long text only produces a `POSSIBLE_DUPLICATE` warning. It never merges records automatically. Resolve it with a documented `duplicate_review`.

Promotion policy is conservative:

- `no`: eligible for positive counts, labels, ranking, and WTP;
- `unclear`: no positive contribution and an uncertainty penalty;
- `yes`: display-only.

Both excluded categories remain in the signal report under cited context excluded from positive counts; exclusion never means silent deletion.

`unknown` authors add no distinct-author count. Counts describe observed pseudonymous account keys, not verified people.

## Evidence labels

| Label | Maximum supportable claim inside this sample |
| --- | --- |
| `unsupported` | No eligible supporting group |
| `anecdotal` | Some eligible support, but fewer than 3 distinct author keys or fewer than 2 threads |
| `recurring` | At least 3 distinct eligible author keys across at least 2 threads |
| `well-corroborated` | At least 6 author keys across 4 threads and 2 communities; 2 distinct costly-behavior types; promotion-risk share no more than 25%; a counterquery linked to this signal; study countersearch marked complete |

Costly behavior types are `workaround`, `switching_friction`, `adoption`, and `observed_payment`. `problem`, `urgency`, `desired_outcome`, and `purchase_intent` matter but do not alone qualify as costly behavior.

The auditor rejects `claimed_level` above the computed ceiling. A researcher may claim a lower level.

## Sample-evidence score

For each signal, let:

- `A` = distinct known origin author keys among eligible support;
- `T` = distinct origin threads;
- `B` = distinct ranked evidence types (`problem`, `urgency`, `workaround`, `switching_friction`, `adoption`, `purchase_intent`, `observed_payment`);
- `C` = distinct origin communities;
- `P` = distinct origin platforms;
- `R` = eligible support published from `as_of - recency_days` through `as_of`, divided by all eligible support;
- `E` = promotion `no` support groups;
- `U` = promotion `unclear` support groups.

The unrounded score is:

```text
min(30, 6*A)
+ min(20, 5*T)
+ min(25, 5*B)
+ min(10, 5*C)
+ min(5, 2.5*P)
+ 10*R
- 20*U/(E+U)    # zero when E+U is zero
```

Clamp to 0-100. Sort by the unrounded score, then evidence ceiling, costly-behavior count, thread count, and signal ID. Display one decimal. Treat differences below five points as practical ties.

Engagement, upvotes, comment counts, follower counts, and author status add zero points. Mutating engagement cannot change a score or rank.

## Counterevidence

Each signal reports both:

- countersearch status: `not_searched`, `partial`, or `complete`;
- counterevidence level: `present`, `none_found_in_coverage`, or `not_established`.

“None found in coverage” never means none exists. Counter sources are not subtracted mechanically from support because positive and counter searches may have different sampling frames. They appear beside the recommendation and should change the interpretation when material.

## Willingness to pay

WTP uses only eligible sources tagged `purchase_intent` or `observed_payment`:

- `none`: no eligible WTP group;
- `anecdotal`: some WTP evidence, but fewer than 3 author keys or fewer than 2 threads;
- `recurring`: at least 3 author keys across at least 2 threads.

The output separately says whether evidence is purchase intent, observed payment, or mixed. Pain, urgency, engagement, and workarounds are never upgraded into WTP.

## Coverage-execution score

The effective target for each dimension is the larger of the declared target and the mode floor. The score awards:

- up to 10 points each for source-group, thread, community, platform, and counterquery target coverage;
- 10 for non-vacuous two-way query/source reconciliation;
- 10 for at least one neutral or counter query;
- 10 when countersearch is marked complete and a counterquery exists;
- 10 when eligible support exists and no thread supplies more than 50%;
- 10 when sources exist and fewer than 25% are promotion `yes` or `unclear`.

Coverage gaps are warnings, not integrity failures. `build` can create a directional report with warnings; `audit --strict` fails until those warnings are resolved. Meeting self-selected coverage does not establish representativeness.
