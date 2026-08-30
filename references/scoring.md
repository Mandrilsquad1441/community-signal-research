# Evidence labels and ranking

The tool keeps three concepts separate:

- **Demand evidence** describes what eligible, deduplicated records in this sample say or demonstrate.
- **Counterevidence status** describes whether disconfirming searches were run and what they found.
- **Coverage-execution score** describes how completely the declared research plan was executed.

None is statistical confidence, population prevalence, market size, revenue potential, or proof that a remote account represents one unique person.

## Independence and eligibility

Hard duplicate groups are formed from explicit `repost_of` links, `same_source` reviews, repeated private `(file hash, record locator)` provenance, equal URL-derived native identities, equal canonical unit URLs, or exact normalized captured text with at least 80 characters **and** 12 words. Exact text with at least 20 characters **and** 4 words that fails either hard condition raises `POSSIBLE_SHORT_EXACT_DUPLICATE`; it needs a documented review and is not merged merely for matching boilerplate. Hard/transitive unions finish before short-exact work is chosen, so no warning appears within a final group. `independent` is pair-specific and non-transitive. Each unresolved short-text class emits one deterministic final-group pair per run until every pair is reviewed; selection is invariant to ledger order. The origin is an explicit repost target when present; otherwise it is the earliest publication timestamp, then lexical source ID. The origin contributes at most one author key, URL-derived thread, community, platform, and recency unit. The report lists every collapsed group's origin, members, and machine-readable reasons.

Five-token shingle similarity over text of at least 30 tokens only produces a `POSSIBLE_DUPLICATE` warning. It never merges records automatically. Resolve it with a documented `duplicate_review`. The scan stops with a strict warning before exceeding 100,000 candidate pairs, 5,000,000 shingle lookups, or 200,000 stored shingle digests.

Promotion policy is conservative:

- `no`: eligible for positive counts, labels, ranking, and WTP;
- `unclear`: no positive contribution and an uncertainty penalty;
- `yes`: display-only.

Both excluded categories remain in the signal report under cited context excluded from positive counts; exclusion never means silent deletion.

`unknown` authors remain visible as ineligible context but add no positive source, author, thread, score, label, concentration, or WTP evidence. Counts describe observed pseudonymous account keys, not verified people.

## Evidence labels

| Label | Maximum supportable claim inside this sample |
| --- | --- |
| `unsupported` | No eligible supporting group |
| `anecdotal` | Some eligible support, but fewer than 3 distinct author keys or fewer than 2 threads |
| `recurring` | At least 3 distinct eligible author keys across at least 2 threads |
| `well-corroborated` | At least 6 author keys across 4 threads and 2 communities; 2 distinct costly-behavior types; promotion-risk share no more than 25%; study countersearch marked complete with a linked qualifying, non-truncated counterquery |

Costly behavior types are `workaround`, `switching_friction`, `adoption`, and `observed_payment`. `problem`, `urgency`, `desired_outcome`, and `purchase_intent` matter but do not alone qualify as costly behavior.

No eligible support is not itself a warning when every cataloged signal is declared `unsupported`. A coverage-complete negative/null study can therefore strict-pass, but it forfeits the eligible-support concentration points and establishes only that no support was found in the searched coverage. Any positive-level declaration without eligible support still triggers `NO_ELIGIBLE_SUPPORT` and the claim-ceiling check.

The auditor rejects `claimed_level` above the computed ceiling. A researcher may claim a lower level.

## Sample-evidence score

For each signal, let:

- `A` = distinct known origin author keys among eligible support;
- `T` = distinct origin threads derived from validated native URLs for Reddit/GitHub/Hacker News, or exact canonical `thread_url` for other public sources;
- `B` = distinct ranked evidence types (`problem`, `urgency`, `workaround`, `switching_friction`, `adoption`, `purchase_intent`, `observed_payment`);
- `C` = distinct origin communities;
- `P` = distinct origin platform families derived from canonical hosts: Reddit, GitHub, Hacker News, `web:<host>`, or `export`; ports remain part of exact source/thread identity but cannot manufacture platform diversity;
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

A query execution qualifies only with at least one viewed result page and, when it saw results, at least one screened result. `complete` additionally requires plan status `complete` and a linked qualifying counterquery with `truncated: false`; truncated, zero-page, or unscreened-result rows cannot establish complete countersearch. Query/source platform and publication-time reconciliation must also pass.

## Willingness to pay

WTP uses only eligible sources explicitly listed in `wtp_citations` and tagged `purchase_intent` or `observed_payment`:

- `none`: no eligible WTP group;
- `anecdotal`: some WTP evidence, but fewer than 3 author keys or fewer than 2 threads;
- `recurring`: at least 3 author keys across at least 2 threads.

The output separately says whether evidence is purchase intent, observed payment, or mixed and repeats the exact cited source excerpts. The free-text `wtp_statement` is a non-rendered researcher rationale and cannot override computed status. Pain, urgency, engagement, and workarounds are never upgraded into WTP.

## Coverage-execution score

The effective target for each dimension is the larger of the declared target and the mode floor. The score awards:

- up to 10 points each for source-group, thread, community, platform, and counterquery target coverage;
- 10 for non-vacuous two-way query/source reconciliation;
- 10 for at least one neutral or counter query;
- 10 when countersearch is marked complete and a qualifying non-truncated counterquery exists;
- 10 when eligible support exists and no thread supplies more than 50%;
- 10 when sources exist and fewer than 25% are promotion `yes` or `unclear`.

Coverage gaps are warnings, not integrity failures. `build` can create a directional report with warnings; `audit --strict` fails until those warnings are resolved. Meeting self-selected coverage does not establish representativeness.
