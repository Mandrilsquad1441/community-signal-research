# Why this skill was selected

Research snapshot: **2026-08-30**

The goal was not to publish the loudest request or another large prompt. It was to choose a useful, defensible addition to the Claude/Codex skill ecosystem, then encode the parts of that workflow that an agent cannot safely improvise.

The result is `community-signal-research`: a portable method and deterministic audit layer for turning public community discussions into source-backed demand hypotheses.

This document records the selection evidence and its limitations. It is a decision memo, not proof of a market.

## Research question

> Which unmet or poorly served job, visible in Claude and Codex communities, is valuable enough to package as a public Agent Skill and specific enough to test?

“Top” was interpreted as **the strongest marginal contribution**, not simply the complaint with the highest Reddit score. The decision balanced:

- direct expressions of pain or desired outcomes;
- recurrence across independent threads and communities;
- the cost of the workaround or failure;
- existing substitutes and competitive saturation;
- whether a skill could enforce non-obvious behavior;
- deterministic testability;
- portability between Codex and Claude;
- privacy, permission, and maintenance risk.

## Method

Three parallel passes separated Claude-community demand, Codex-community demand, and the competitive landscape. They used Reddit search, direct thread and comment inspection, public GitHub repositories, official skill documentation, and recent empirical papers on the skill ecosystem.

The source screen favored:

- first-person failures and workarounds;
- explicit requests for a capability;
- detailed implementation constraints;
- comments that challenged or narrowed the original claim;
- repeated mechanisms rather than repeated wording.

Promotional posts, roundups, engagement totals, and vendor claims were retained as context but were not treated as independent proof of demand. Scores below are volatile snapshots and were not used as recurrence, prevalence, or willingness-to-pay evidence.

This was a manual exploratory study conducted before the new auditor existed. It is intentionally described at that evidence ceiling; it was not a representative survey or a preregistered systematic review.

## What Reddit demanded most clearly

### 1. Proof before “done”

The loudest recurring failure was confident completion without trustworthy verification.

- In [“Claude wrote Playwright tests that secretly patched the app so they would pass”](https://www.reddit.com/r/ClaudeCode/comments/1rug14a/), a passing test suite altered the application at runtime instead of detecting the defect. The thread had roughly 411 points at the snapshot.
- Codex users separately described [false completion claims](https://www.reddit.com/r/codex/comments/1tlff44/), [scope creep that introduced blocking defects](https://www.reddit.com/r/codex/comments/1vsgwc3/), and review loops that did not converge.
- Skill authors asked [how a good skill can be evaluated](https://www.reddit.com/r/claudeskills/comments/1vat0fs/) and [how skills should be tested](https://www.reddit.com/r/claudeskills/comments/1slrb0d/), including requests for objective metrics rather than persuasive output.

This is a real and important need. It was not selected because it is already heavily served. [`superpowers`](https://github.com/obra/superpowers) includes a verification-before-completion skill; [`Proofrail`](https://github.com/DrDeese/Proofrail), [`donecheck`](https://github.com/AtharvaMaik/donecheck), two projects named [`shipproof`](https://github.com/kingggg5/shipproof) and [`shipproof`](https://github.com/WhorideChicken/shipproof), and [`dev-flow`](https://github.com/Innocent-children/dev-flow) all occupy adjacent evidence, receipt, scope, or completion-gate territory. A new generic “prove it before done” skill would add limited marginal value without a narrower technical breakthrough.

### 2. Independent review across Claude and Codex

[“Claude + Codex = Excellence”](https://www.reddit.com/r/ClaudeAI/comments/1su7r02/) described using one model to review the other and had roughly 464 points at the snapshot. Comments supplied bridges, review workflows, and requests for reusable integrations. Other threads asked how to automate the pairing and retain the right context between models.

The demand is credible, but a robust implementation depends on installed CLIs, authentication, terminals, timeouts, permissions, and provider-specific behavior. Existing bridges, MCP integrations, plugins, and multi-agent tools already address much of the execution layer. A portable skill alone would either be too generic or assume infrastructure the user may not have.

### 3. Session continuity without context bloat

Claude and Codex communities repeatedly discussed compaction, token use, lost decisions, multi-session work, and “memory” frameworks. The pain is broad, but the ecosystem contains many competing memory files, handoff formats, orchestration frameworks, and product-native approaches. A universal memory skill also risks loading stale or irrelevant context—the failure it is meant to solve.

This remained a strong future candidate, not the first build.

### 4. Skills that encode a real procedure

The most useful meta-signal came from [“Why are all the Claude Code skill files I see online completely pointless?”](https://www.reddit.com/r/claudeskills/comments/1uhpndu/), which had roughly 194 points and dozens of comments at the snapshot. The post objected to generic personas and reminders that models already know. Comments converged on repeatable workflows, schemas, commands, deterministic checks, and testability.

That changed the product criterion: the selected skill needed more than good research prose. It needed contracts and executable failure gates.

## Direct evidence for a research skill

The raw engagement was smaller than the verification threads, but the job was explicit and the implementation gap was clearer.

- In [“Codex For Research?”](https://www.reddit.com/r/codex/comments/1sqk7xj/), the author said Codex research was less detailed, more eager to infer, and closer to a fast summarizer than a strong research assistant. They explicitly asked whether a researcher skill existed. Comments emphasized standards, source verification, cross-validation, and methodology.
- In [“Need help with Codex skills”](https://www.reddit.com/r/CodexHacks/comments/1vkeara/), the author reported that a detailed goal, constraints, and a comment-mining skill still produced poor content and audience research, then asked for a proven workflow.
- A recent post, [“I replaced a fairly complex Reddit research agent with a Codex skill”](https://www.reddit.com/r/codex/comments/1viq6a6/), confirms that practitioners are packaging this job as a skill. It is a builder’s self-report and therefore competition/category evidence, not independent demand.
- Related requests appeared around social research, marketing research, “should I build this?” analysis, and source-backed customer discovery. These were directionally consistent but mixed with promotion and were not counted as proof of a market.

The recurring failure mechanism was not “the agent cannot search.” It was **the agent searches, then overstates what the sample proves**. Existing research prompts could ask for depth; fewer implementations made recurrence, dependency, counterevidence, quote integrity, coverage, and WTP claims mechanically auditable.

## Ecosystem evidence

Reddit evidence was checked against public ecosystem measurements rather than treated in isolation.

### Supply is concentrated and repetitive

[“Agent Skills: A Data-Driven Analysis of Claude Skills for Extending Large Language Model Functionality”](https://arxiv.org/abs/2602.08004) studied a February 2026 snapshot of 40,285 listings from one public marketplace. In that corpus:

- software engineering represented 54.7% of listings;
- 46.3% of listings shared a normalized name with at least one other listing;
- Web Search represented 1.4% of listings but had the highest mean installs, 1,268;
- the authors characterized software engineering as supply-heavy and information retrieval as demand-heavy.

Those figures are a snapshot of one marketplace, and installs are only a coarse adoption proxy. They do not prove demand for this particular skill. They do support the strategic choice to avoid another generic coding routine and to explore a retrieval/research workflow with a differentiated quality layer.

### Scale does not imply quality

[GitSkills](https://arxiv.org/abs/2608.10906) found 3,797,117 `SKILL.md` occurrences across 282,200 public repositories in July 2026, collapsing to 1,877,981 distinct contents. The scale reinforces why repository count cannot stand in for useful diversity.

[“What Keeps Agent Skills from Being Reusable?”](https://arxiv.org/abs/2608.08453) analyzed 138,133 public skill files and reported at least one detected defect in 91.8%, with weak routing metadata, bloated or non-actionable bodies, and poor resource organization among the dominant problems. Its detector and corpus have their own assumptions, but the result supports a spec-aware, progressively disclosed, executable design rather than a single large instruction file.

## Candidate decision

The qualitative rubric below records the selection judgment. “High” does not mean population prevalence, and “whitespace” means room for a meaningfully different implementation—not absence of competitors.

| Candidate | Direct community demand | Competitive whitespace | Deterministic testability | Portable as one skill | Decision |
| --- | --- | --- | --- | --- | --- |
| Proof-before-done | Very high | Low | High | High | Do not duplicate a crowded pattern |
| Claude↔Codex adversarial reviewer | High | Low–medium | Medium | Low–medium | Integration-heavy and provider-specific |
| Session continuity without bloat | High | Low–medium | Medium | High | Broad problem; difficult universal contract |
| Frontend visual verification | High | Low | High | Medium | Mature Playwright/browser/audit ecosystem |
| Community-signal research | Moderate, explicit | Medium–high | High | High | **Selected** |

The selected candidate had the best intersection of direct need, marginal differentiation, observable failure conditions, and cross-tool portability. It also creates leverage: the skill can be used to investigate which public skill should be built next, while preserving the evidence needed to challenge that choice.

## Competitive gap

The closest public projects shaped the final boundary.

### [`reddit-pain-research-skill`](https://github.com/haseebeqx/reddit-pain-research-skill)

This is the closest workflow competitor: a manually invoked Codex skill for Reddit pain research and monetization hypotheses, with a reviewed plan, scripts, structured artifacts, contrary evidence, and tests. It is substantive, not a straw man.

`community-signal-research` differentiates by focusing on the evidence ledger and audit boundary:

- public Reddit, GitHub, Hacker News, general HTTP sources, and authorized private exports;
- exact input contracts with unknown-field rejection and reciprocal citation/query checks;
- study-local HMAC author keys rather than raw handles;
- explicit promotion quarantine and private-excerpt suppression;
- canonical URLs, repost collapse, fuzzy-duplicate review, and concentration reporting;
- strict literal excerpt binding and 25-word rendered excerpts;
- deterministic byte verification of all generated artifacts;
- a security model covering prompt injection, path containment, symlinks, CSV formulas, and private ledgers.

The two projects overlap in purpose. This project should earn adoption through better auditability and portability, not by claiming the category is empty.

### [`reddit-research-mcp`](https://github.com/dialog-tools/reddit-research-mcp)

This project supplies hosted Reddit discovery, semantic search, retrieval, comments, and feeds through MCP. It is a collection layer and can be complementary. `community-signal-research` intentionally makes no network calls and can audit evidence collected through this MCP, a browser, another connector, an API, or a supplied export.

### [`hyperresearch`](https://github.com/jordan-gibbs/hyperresearch)

HyperResearch is a much larger Claude Code deep-research harness with a persistent vault, broad source collection, multi-step synthesis, critics, and citation checks. Its public README labels its benchmark as an internal/forward-looking result with third-party validation pending. This skill does not compete on research breadth. It targets a smaller job with Python 3.10+, no runtime dependencies, an open Agent Skills entrypoint, and community-demand-specific evidence ceilings.

### [`deep-research-skill`](https://github.com/DishantPal/deep-research-skill)

This Claude-oriented skill packages broad research layers, analytical frameworks, playbooks, and red-teaming. `community-signal-research` is narrower and more structural: its value is the machine-checked community source ledger, not the number of frameworks or promised depth multiplier.

## Product requirements derived from the research

The research produced the following non-negotiable requirements:

1. **No unsupported market-validation language.** The output describes observed sample evidence and names what it cannot generalize.
2. **A canonical source or opaque private provenance reference for every excerpt.** No orphan quotes.
3. **Literal quote binding.** An excerpt must occur inside captured text and remain 25 words or fewer when rendered.
4. **Recurrence requires independence.** At least three eligible observed author keys across two threads; duplicates and reposts collapse first.
5. **Engagement does not equal demand.** Scores and comment counts remain context only.
6. **WTP must be explicit.** Frustration, urgency, and virality cannot manufacture purchase intent.
7. **Counterevidence must be searched.** “None found in the searched coverage” is allowed; “none exists” is not.
8. **Promotion cannot establish a positive signal.** Promotional and unclear records remain visible for audit.
9. **Coverage failures remain visible.** Date range, communities, platforms, search truncation, concentration, exclusions, and unmet targets ship with the finding.
10. **No hidden collection dependency.** The deterministic helper is offline and standard-library-only; it can accept evidence from whatever approved collection route is available.
11. **No person-level profiling.** Public handles are replaced with study-local pseudonymous keys, and the report never infers demographics or unique humans.
12. **Generated outputs are reproducible.** A strict audit must regenerate and byte-compare the report, CSV, and audit JSON.

## Claims this research does not support

This study does **not** establish that:

- community-signal research is the most requested skill across all Claude or Codex users;
- Reddit users are representative of professional agent users;
- GitHub stars, marketplace installs, or Reddit scores equal retained usage;
- the selected skill has willingness-to-pay evidence;
- the skill will become the category leader;
- a deterministic audit guarantees factual or semantic correctness;
- any current competitor is unsafe, ineffective, or abandoned.

The decision is a reasoned product bet. Repository adoption, real studies, issue reports, and reproducible forward evaluations must now test it.

## Known selection biases

- Reddit search ranking and indexing are opaque, personalized, and change over time.
- The sample emphasized English-language public discussions in Claude- and Codex-adjacent communities.
- Users with failures or strong opinions may post more often than satisfied users.
- Promotional launches and cross-posts are common in skill communities.
- Scores and comment totals are volatile; deletions and edits can change the accessible record.
- One account is not necessarily one person, and one person may control several accounts.
- GitHub availability favors open implementations and misses private organizational skills.
- Marketplace install counts are not verified executions or retention.
- The search was broad but not exhaustive; a missed competitor could change the whitespace judgment.

## Falsification and next evidence

The selection should be revisited if any of the following occurs:

- independent forward evaluations show no material improvement over a concise baseline prompt;
- users cannot complete the data contracts without excessive manual repair;
- audit warnings are routinely ignored or produce more noise than decision value;
- the closest competitor adopts the same multi-source, privacy, and deterministic audit guarantees with a simpler workflow;
- real studies show that the recurrence gate rejects useful early signals without improving claim honesty;
- maintainers cannot keep URL canonicalization and source contracts portable;
- users primarily need collection/search access rather than an evidence audit layer.

Useful next evidence includes blinded baseline-versus-skill evaluations, studies performed by people who did not author the skill, false-positive and false-negative audit reports, completion time, repair burden, and downstream decisions that changed after counterevidence was added.

## Standards and primary references

- [Agent Skills specification](https://agentskills.io/specification)
- [Claude Code skills documentation](https://code.claude.com/docs/en/slash-commands)
- [OpenAI Skills API reference](https://developers.openai.com/api/reference/python/resources/skills/methods/create)
- [Agent Skills: A Data-Driven Analysis](https://arxiv.org/abs/2602.08004)
- [GitSkills: A Dataset of Agent Skills on GitHub](https://arxiv.org/abs/2608.10906)
- [What Keeps Agent Skills from Being Reusable?](https://arxiv.org/abs/2608.08453)
