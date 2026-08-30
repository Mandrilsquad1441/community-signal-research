# Community signal findings

Input fingerprint: `sha256:d2f965d0d5837c36405ddceaa2607f25f5f68de76c3f27a2a5d28b67ad747214`

**Decision:** Decide whether to build and publish community\-signal\-research\.

**Research question:** What evidence supports an auditable community\-research skill for coding agents?

**Evidence cutoff:** 2026\-08\-30 | **Mode:** `quick` | **Coverage-execution score:** 100.0/100

> This report ranks evidence observed in the declared sample. It does not estimate market size, prevalence, revenue, or population-level demand. Engagement is not scored.

> The offline audit proves internal ledger consistency and reproducible output, not remote-page authenticity, account identity, semantic classification, search completeness, or representativeness.

## Ranked hypotheses

| Rank | Signal | Evidence label | Evidence score | Author keys | Threads | Excluded cited support | Counter sources | WTP |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | More reliable community and content research for coding agents | `recurring` | 65.5 | 3 | 2 | 0 | 1 | `none` |
| 2 | Skills that encode a testable procedure | `anecdotal` | 62.5 | 5 | 1 | 0 | 0 | `none` |

## 1. More reliable community and content research for coding agents

**Hypothesis:** Coding\-agent users need a community and content research workflow that reduces generic or assumption\-heavy results and binds decisions to traceable evidence\.

**Decision relevance:** A portable Agent Skill can fill the gap without requiring a hosted scraper or proprietary research harness\.

**Evidence ceiling:** `recurring` from 3 distinct observed author keys across 2 threads and 2 communities. Score 65.5/100 within this sample.

**Countersearch:** `complete`; counterevidence `present`. **Costly behavior observed:** adoption, workaround.

### Supporting evidence

> "Is there a researcher skill people here use with Codex?" - [src\-codex\-research\-post](https://www.reddit.com/r/codex/comments/1sqk7xj/codex_for_research/), 2026\-04\-20; `problem`, `desired\_outcome`

> "But, it is really necessary: standards for everythings, scales, metrics, evaluaciones, cross validations, etc\." - [src\-codex\-standards](https://www.reddit.com/r/codex/comments/1sqk7xj/comment/oh8r9ar/), 2026\-04\-20; `workaround`, `adoption`, `constraint`

> "I always get reasoning bleed in the final document" - [src\-codex\-underwhelming](https://www.reddit.com/r/codex/comments/1sqk7xj/comment/oheyczg/), 2026\-04\-21; `problem`, `workaround`

> "it pulled me comment mining skill still same shit level result" - [src\-codexhacks\-poor\-research](https://www.reddit.com/r/CodexHacks/comments/1vkeara/need_help_with_codex_skills/), 2026\-08\-10; `problem`, `workaround`

> "someone else skill&\#x27;s that are proven and battel testing" - [src\-codexhacks\-tested\-skill](https://www.reddit.com/r/CodexHacks/comments/1vkeara/comment/p2tdxub/), 2026\-08\-10; `desired\_outcome`, `constraint`

### Counterevidence

> "Overall though it is working well as a research assistant" - [src\-codex\-existing\-workflow](https://www.reddit.com/r/codex/comments/1sqk7xj/comment/ohaa01a/), 2026\-04\-20; `satisfaction`, `adoption`

### What could falsify or reframe this

- The observed need may be satisfied by better prompts or bespoke multi\-agent workflows rather than a reusable skill\.
- A purposive Reddit sample can overrepresent users motivated to complain or optimize their workflows\.
- Evidence needed: Cold comparisons showing that a simpler prompt or existing public skill produces equally traceable decisions with fewer errors and less effort\.

## 2. Skills that encode a testable procedure

**Hypothesis:** Skill users value repeatable procedures, schemas, and objective tests more than generic expertise personas or reminders\.

**Decision relevance:** The research skill should include concrete data contracts and an executable audit rather than prose alone\.

**Evidence ceiling:** `anecdotal` from 5 distinct observed author keys across 1 threads and 1 communities. Score 62.5/100 within this sample.

**Countersearch:** `complete`; counterevidence `none_found_in_coverage`. **Costly behavior observed:** adoption.

### Supporting evidence

> "To really evaluate you need objective metrics, real data, some Benchmarks\." - [src\-skills\-benchmarks](https://www.reddit.com/r/claudeskills/comments/1uhpndu/comment/oub9zyn/), 2026\-06\-28; `problem`, `desired\_outcome`, `constraint`

> "The whole point of a skill is to fix something Claude consistently gets wrong\." - [src\-skills\-post](https://www.reddit.com/r/claudeskills/comments/1uhpndu/why_are_all_the_claude_code_skill_files_i_see/), 2026\-06\-28; `problem`, `constraint`

> "Skills help repeatable tasks return consistent information\." - [src\-skills\-repeatability](https://www.reddit.com/r/claudeskills/comments/1uhpndu/comment/ouamwi4/), 2026\-06\-28; `desired\_outcome`, `adoption`

> "Skills are schemas; &quot;You are an expert at this&quot; is not a schema\." - [src\-skills\-schema](https://www.reddit.com/r/claudeskills/comments/1uhpndu/comment/ouhevrp/), 2026\-06\-29; `constraint`

> "The basic problem for skills or agents or prompts is how do you test them?" - [src\-skills\-testing](https://www.reddit.com/r/claudeskills/comments/1uhpndu/comment/oua2e90/), 2026\-06\-28; `problem`, `desired\_outcome`

### Counterevidence

No counterexample was found in the searched coverage; this does not mean none exists.

### What could falsify or reframe this

- The discussion comes from one thread and may reflect a local norm rather than a recurring cross\-thread need\.
- Users may prefer short reminder\-style skills when the task is simple\.
- Evidence needed: Independent threads or controlled comparisons showing generic persona skills are equally consistent and testable\.

## Scope, search coverage, and limitations

- Date window: 2025\-08\-30 through 2026\-08\-30; evidence cutoff 2026\-08\-30.
- Platforms in scope: reddit.
- Communities in scope: r/codex, r/CodexHacks, r/claudeskills.
- Languages in scope: en.
- Inclusion criteria: First\-person research pain, requested research capability, repeatable workflow, evaluation need, or working counterexample; Direct post or comment permalink with visible publication and engagement snapshot metadata.
- Exclusion criteria: Generic tool praise without a research or skill\-quality mechanism; Promotion, copied summaries, and comments without enough context to classify.
- Source Units: 11 observed / 8 target.
- Threads: 3 observed / 3 target.
- Communities: 3 observed / 3 target.
- Platforms: 1 observed / 1 target.
- Counter Queries: 2 observed / 1 target.
- Truncated queries: 2 of 4.
- Largest eligible-support thread share: 50%.
- Promotional or unclear source share: 0%.
- Declared limitation: Reddit search ranking is opaque and the sample is purposive rather than representative\.
- Declared limitation: The study demonstrates the method on a narrow product decision and does not estimate total market demand\.
- Declared limitation: Observed account keys cannot establish that each account maps to a unique person\.
- Coverage note: All four Reddit searches were manually inspected; two result sets were truncated after one page\.
- Coverage note: Engagement snapshots are retained for context and excluded from scoring\.

### Query ledger

| Query ID | Run at | Platform | Intent | Query | Sort | Seen / screened | Pages | Truncated | Included units |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | ---: |
| `qry\-existing\-solutions` | 2026\-08\-30T12:40:00Z | reddit | `counter` | Codex research working well existing research system alternative | best and new comments | 13 / 13 | 1 | no | 1 |
| `qry\-research\-skill` | 2026\-08\-30T12:00:00Z | reddit | `support` | Codex research skill detailed sources assumptions | relevance and best comments | 24 / 10 | 1 | yes | 6 |
| `qry\-skill\-quality` | 2026\-08\-30T12:20:00Z | reddit | `neutral` | Claude skills pointless repeatable test benchmarks schemas | relevance and best comments | 30 / 15 | 1 | yes | 5 |
| `qry\-skill\-substitutes` | 2026\-08\-30T12:45:00Z | reddit | `counter` | Claude skills generic prompt enough no schema no testing | relevance and new | 9 / 9 | 1 | no | 0 |

## Interpretation and next action

- Cited observation (`src\-codex\-research\-post`, `src\-codex\-underwhelming`, `src\-codexhacks\-poor\-research`, `src\-codexhacks\-tested\-skill`): Users explicitly ask for stronger research skills, specialized depth, verified sources, and proven workflows\.
- Cited observation (`src\-skills\-post`, `src\-skills\-repeatability`, `src\-skills\-schema`, `src\-skills\-testing`, `src\-skills\-benchmarks`): A separate skill\-quality thread calls for repeatability, schemas, objective evaluation, and benchmarks rather than generic expertise personas\.
- Cited observation (`src\-codex\-existing\-workflow`): One reported custom Codex research system works well, showing the job can be solved without this particular packaged skill\.
- Researcher inference (`sig\-auditable\-research`, `sig\-procedural\-skill\-quality`): The defensible product wedge is auditability and community\-evidence integrity, implemented as a testable procedure rather than generic deep\-research prose\.

**Recommendation (`sig\-auditable\-research`, `sig\-procedural\-skill\-quality`):** Build and publish a portable, local\-first skill with deterministic provenance, independence, WTP, counterevidence, and coverage gates, then evaluate it against a no\-skill baseline\.
- Recommendation caveat: This quick purposive study supports a prototype decision, not a market\-size or population\-demand claim\.
- Recommendation caveat: The research\-quality signal is recurring inside this sample; the procedural\-skill signal remains anecdotal because every supporting record comes from one thread\.
- Recommendation caveat: Neither signal establishes willingness to pay\.

**Next tests:**
- Run preregistered cold baseline\-versus\-skill trials on frozen adversarial community datasets\.
- Repeat the demand study on GitHub Issues or Discussions and Hacker News to test source diversity\.

**Stop reason:** The quick\-mode source, thread, community, platform, and counterquery floors were reached; further work is assigned to the preregistered cold evaluation and cross\-platform follow\-up\.
