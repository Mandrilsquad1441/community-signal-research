# Data contracts

The helper reads UTF-8 JSON and JSONL, rejects duplicate object keys, unknown fields, non-finite numbers, numeric tokens above 256 characters/digits, and nesting deeper than 100 arrays/objects, and writes deterministic UTF-8/LF artifacts. Each input is opened once with `O_NOFOLLOW` where available, validated as the same regular pathname/descriptor before and after, and read to at most 10 MiB plus one detection byte. Growth, identity/location replacement, link/reparse substitution, or size/mtime/ctime change during the read fails. Each JSONL file is capped at 10,000 records and 11,000 physical lines including blanks/malformed rows; lists and object fields at 10,000; JSON object keys and general strings at 20,000 characters; captured text at 100,000 characters; all `support_citations`, `counter_citations`, and `wtp_citations` lists together at 50,000 references; validation output at 10,000 issues plus one limit error; and each generated artifact at 32 MiB. General schema strings rejected for size, invalid Unicode, controls, or bidirectional formatting are replaced with inert empty values before normalization, regular-expression matching, URL parsing, or other semantic work. Over-limit string/object-key errors do not echo the rejected content.

Shared semantic/display normalization is fixed to the standard library's Unicode 3.2 NFKC database, this explicit whitespace set—`U+0009`–`U+000D`, `U+0020`, `U+0085`, `U+00A0`, `U+1680`, `U+180E`, `U+2000`–`U+200A`, `U+2028`, `U+2029`, `U+202F`, `U+205F`, and `U+3000`—and ASCII `A`–`Z` case mapping. Runs therefore do not inherit newer Python Unicode classifications. Characters introduced after Unicode 3.2 remain unchanged, and non-ASCII case variants remain distinct unless another explicit identity rule joins them.

Dates use exactly `YYYY-MM-DD`; timestamps use exactly `YYYY-MM-DDTHH:MM:SS[.1-6 digits](Z|±HH:MM)`, with valid calendar, clock, and offset values. The lexical caps are 10 and 64 characters. Lexemes receive bounded screening before ISO parsing, invalid results are replaced before cross-record analysis, and generic errors do not echo the rejected value. These pinned grammars do not vary with Python's accepted `fromisoformat` spellings.

Interrupted-recovery discovery scans no more than 10,000 study-directory entries. Before writing other study files, `init` appends `.author-key`, `.csr-build.lock`, `.csr-*.tmp`, `.csr-artifacts-stage-*/`, and `.csr-artifacts-backup-*/` as the final `.gitignore` block so earlier negations cannot expose them. It reads an existing ignore file with the stable descriptor primitive and a 1 MiB-plus-one-byte bound, and refuses a post-append result above 1 MiB. IDs use `qry-*`, `src-*`, and `sig-*` prefixes.

Run `init` to create exact templates. The abbreviated examples below show every required field but only one record.

## `study-plan.json`

```json
{
  "schema_version": "1.0",
  "study_id": "agent-skill-demand-2026",
  "question": "Which unmet agent-skill job should we build?",
  "decision": "Choose one public skill to implement first.",
  "mode": "standard",
  "as_of": "2026-08-30",
  "recency_days": 365,
  "date_window": {"start": "2025-08-30", "end": "2026-08-30"},
  "population": "People using coding agents for real work",
  "scope": {
    "platforms": ["reddit", "github"],
    "communities": ["r/codex", "r/ClaudeCode", "owner/repository"],
    "languages": ["en"]
  },
  "inclusion_criteria": ["First-person pain, request, workaround, adoption, constraint, satisfaction, or explicit purchase evidence"],
  "exclusion_criteria": ["Untraceable summaries and promotion without independent evidence"],
  "coverage_targets": {"source_units": 25, "threads": 8, "communities": 3, "platforms": 2, "counter_queries": 2},
  "counterevidence_status": "complete",
  "stop_condition": "Coverage met or two successive query families add no new mechanism",
  "limitations": ["Reddit search ranking and result truncation are opaque"]
}
```

Allowed modes are `quick` and `standard`. Minimum effective targets are 8/3/1/1/1 for quick and 25/8/3/2/2 for standard, in source/thread/community/platform/counterquery order. Lower declared targets never manufacture a perfect score. Allowed counterevidence statuses are `planned`, `partial`, and `complete`.

## `query-log.jsonl`

```json
{"schema_version":"1.0","id":"qry-001","platform":"reddit","query":"\"is there a skill\" research","intent":"neutral","run_at":"2026-08-30T12:00:00Z","sort":"relevance","results_seen":20,"results_screened":10,"pages_seen":1,"truncated":true,"included_source_ids":["src-001"],"signal_ids":["sig-001"],"notes":"Reddit web search"}
```

`intent` is `support`, `counter`, or `neutral`. Counts are non-negative integers, every execution has `pages_seen >= 1`, screened results cannot exceed seen results, and any row with `results_seen > 0` must have `results_screened > 0`. A query with included source IDs must report at least one seen and screened result. A query can include several atomic comments from one screened thread, so inclusions may exceed screened results.

Every source/query inclusion must reconcile in both directions. The linked rows must use the same platform after the pinned Unicode/whitespace/ASCII-case normalization (`hn` aliases to `hackernews`), and `source.published_at` cannot be later than `query.run_at`. An execution is unique by normalized platform, normalized query, UTC-normalized run time, and normalized sort; duplicate rows fail even if `intent` differs because intent is a classification, not a second execution. Link a counterquery to every signal it attempts to disconfirm, even if it returns no included sources. A query qualifies when it has a viewed page and, if it saw results, screened at least one. Only a qualifying counterquery with `truncated: false`, combined with plan status `complete`, establishes complete countersearch; otherwise the status is partial or not searched.

## `source-ledger.jsonl`

Public source:

```json
{"schema_version":"1.0","id":"src-001","platform":"reddit","community":"r/codex","source_type":"post","url":"https://www.reddit.com/r/codex/comments/example/research/","record_ref":null,"visibility":"public","capture_method":"browser","source_file_sha256":null,"thread_url":"https://www.reddit.com/r/codex/comments/example/research/","unit_id":"reddit:t3_example","thread_id":"reddit:t3_example","published_at":"2026-05-01T10:00:00Z","collected_at":"2026-08-30T12:05:00Z","author_key":"author:7196b9a20d5b8a3e","language":"en","source_status":"available","title":"Research workflow","captured_text":"I need a research skill that verifies every source and checks counterexamples.","excerpt":"I need a research skill that verifies every source","stance":"support","evidence_types":["desired_outcome"],"promotional":"no","repost_of":null,"query_ids":["qry-001"],"signal_ids":["sig-001"],"engagement":{"score":12,"comments":5,"snapshot_at":"2026-08-30T12:05:00Z"},"duplicate_reviews":[],"notes":""}
```

Supplied private export:

```json
{"schema_version":"1.0","id":"src-002","platform":"export","community":"authorized-customer-export","source_type":"export_record","url":null,"record_ref":"survey.csv:row-17","visibility":"supplied_private","capture_method":"export","source_file_sha256":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","thread_url":null,"unit_id":"export:row-17","thread_id":"export:response-17","published_at":"2026-06-02T09:00:00Z","collected_at":"2026-08-30T12:10:00Z","author_key":"unknown","language":"en","source_status":"available","title":"","captured_text":"The authorized supplied response text.","excerpt":"The authorized supplied response text.","stance":"support","evidence_types":["problem"],"promotional":"no","repost_of":null,"query_ids":["qry-002"],"signal_ids":["sig-001"],"engagement":{"score":null,"comments":null,"snapshot_at":"2026-08-30T12:10:00Z"},"duplicate_reviews":[],"notes":"Private excerpt is never rendered in generated findings."}
```

For public records, `url` and `thread_url` are required canonical HTTP(S) URLs. Canonicalization removes RFC 3986 dot segments, decodes percent-escaped unreserved bytes, converts raw Unicode and unsafe component bytes to uppercase UTF-8 percent escapes, lowercases and removes a trailing dot from the host, compresses IP literals to one lowercase spelling, and removes default ports. Hosts must be ASCII: supply an explicit valid IDNA A-label such as `xn--...`, never a raw-Unicode host; bracketed hosts must be supported IPv6 literals. Browser-dependent backslashes, malformed path or generic query/fragment escapes, URL userinfo, credential/session/signature-like query, fragment, or path-matrix keys (including bracketed, nested, dotted, semicolon-separated, compound, compact/camel, and up-to-three-layer percent-encoded spellings), email material, and contact-shaped parameter values fail. Compact/camel credential examples include `token`, `session`, `privateKey`, `secretKey`, `signingKey`, and `keyPairId`. Obvious loopback/local/private/link-local/multicast/legacy-numeric/single-label hosts fail, as do the apex and all descendants of `home.arpa`, `internal`, `lan`, `local`, `localdomain`, and `localhost`. The offline helper does not resolve DNS or prove public routability.

Literal-address screening uses project-pinned ranges rather than Python's version-dependent `is_global` result. IPv4 rejects `0.0.0.0/8`, `10.0.0.0/8`, `100.64.0.0/10`, `127.0.0.0/8`, `169.254.0.0/16`, `172.16.0.0/12`, `192.0.0.0/24`, `192.0.2.0/24`, `192.88.99.0/24`, `192.168.0.0/16`, `198.18.0.0/15`, `198.51.100.0/24`, `203.0.113.0/24`, `224.0.0.0/4`, and `240.0.0.0/4`. IPv6 rejects `::/96`, `::ffff:0:0/96`, `64:ff9b::/96`, `64:ff9b:1::/48`, `100::/64`, `2001::/23`, `2001:db8::/32`, `2002::/16`, `3ffe::/16`, `3fff::/20`, `fc00::/7`, `fe80::/10`, `fec0::/10`, and `ff00::/8`.

Native families apply additional rules and reject nondefault ports rather than silently rewriting them to official origins. Port text is bounded to five characters before integer conversion. Reddit aliases become `https://www.reddit.com`, tracking/query/fragment material is removed, subreddit spelling is case-normalized, and positive ASCII base-36 post/comment IDs of at most 32 characters are lowercased with leading zeros removed before deriving `reddit:t3_*` and `reddit:t1_*` identities. Only exact host `github.com` receives GitHub-native handling; its positive root issue/discussion number of at most 20 digits or recognized direct comment fragment normalizes leading zeros in the root number and derives repository/kind/number identities. GitHub subdomains remain generic. Hacker News requires `https://news.ycombinator.com/item?id=<positive ASCII integer>` with at most 20 digits, keeps one normalized item ID, derives `hackernews:item:<id>`, requires a story URL to equal its thread URL, and rejects a comment that names itself as its root. Reddit and GitHub URLs bind comments to their parent; an HN comment URL does not encode the root story, so that relationship remains a declared, remotely unverified fact.

For other public platforms, RFC normalization still applies but server-significant path case, repeated slashes, a trailing path slash, query-pair order, and fragments are preserved. `unit_id` must exactly equal canonical `url`, `thread_id` must exactly equal canonical `thread_url`, and both URLs must use the same canonical host and explicit port. `record_ref` and `source_file_sha256` are null. Submitted identifiers therefore cannot manufacture identity strings, but declared HN/generic parent relationships still require source review. Platform-diversity metrics ignore arbitrary generic platform spelling and ports: they use native host families, `web:<canonical-host>` for generic sources, and `export` for private sources. Ports remain part of exact URL/unit/thread identity. Rows that resolve to one public canonical unit are collapsed; materially conflicting copies fail and must become one authoritative row.

For supplied-private records, `platform` is exactly `export`, `source_type` is `export_record`, `capture_method` is `export`, URLs are null, `record_ref` is an opaque 1-256 character locator without obvious email/phone data, and a caller-declared source-file SHA-256 is required. The helper validates and preserves that digest but does not receive the file and cannot authenticate it. Never fabricate a URL for a private record. An exactly repeated `(source_file_sha256, record_ref)` pair collapses to one unit. Its material-conflict comparison treats Unicode/whitespace/case-equivalent `community` and `language`, UTC-equivalent `published_at`, and reordered `evidence_types` as equal; opaque `unit_id`/`thread_id` and other material fields remain exact. A material difference still fails.

Allowed source types: `post`, `comment`, `issue`, `discussion`, `story`, `review`, `export_record`, `other`.

Allowed source statuses: `available`, `edited`, `deleted`, `unavailable`, `unknown`. Status is a declared snapshot fact; the offline auditor cannot verify it remotely.

Allowed evidence types:

- `problem`: explicit failure, cost, or pain;
- `desired_outcome`: explicit requested progress or capability;
- `workaround`: existing manual or improvised behavior;
- `urgency`: explicit time pressure or material consequence;
- `switching_friction`: changing or being unable to change solutions;
- `adoption`: actual use, trial, or installation;
- `purchase_intent`: explicit future buying, price, or budget intent;
- `observed_payment`: actual payment, purchase, or allocated budget;
- `constraint`: a requirement that bounds a solution;
- `satisfaction`: evidence that an existing solution is sufficient.

Allowed promotion values are `yes`, `no`, and `unclear`. Only a source with promotion `no` and a known pseudonymous author is eligible for positive counts, labels, scoring, or WTP. Unknown-author, `unclear`, and `yes` sources remain visible but add no positive evidence; `unclear` also incurs an uncertainty penalty.

`author_key` is exactly `unknown` or `author:` plus 16-64 lowercase hexadecimal characters. Use `author-key --study-dir <study> --platform <observed-platform>`; the normalized platform is part of the HMAC input, so an equal handle on two platforms produces different keys. Handle input is capped at 4,096 UTF-8 bytes. `.author-key` is accepted only as a regular, non-symlinked 64-lowercase-hex secret with an optional LF or CRLF and is read with a fixed upper bound. Do not publish it. Keys are pseudonyms for observed accounts, not proof of unique people or anonymity.

`excerpt` must be a literal substring of `captured_text`, at most 25 whitespace-delimited words, and at most 500 characters. Dangerous C0/C1 control characters other than ordinary tab, newline, and carriage return, bidirectional-formatting controls, invalid Unicode surrogates, and over-limit strings are rejected from keys and user-controlled values. `repost_of` points directly to an origin whose own value is null and whose publication time is no later than the repost; chains, self-links, missing targets, cycles, and reversed chronology fail. Duplicate signal sources should cite the group origin.

Fuzzy candidate review is machine-readable:

```json
"duplicate_reviews": [{"other_source_id":"src-009","decision":"independent","reason":"Different authors describe separate implementations with similar boilerplate."}]
```

Allowed decisions are `same_source` and `independent`. `same_source` collapses the pair. For short-exact review, `independent` clears only the pair after the reviewed IDs are mapped to their final groups; independence is not transitive. A fuzzy warning is cleared only by a review naming that exact warned source-ID pair. Exact normalized text is collapsed automatically only at **80 or more characters and 12 or more words**. Exact normalized text at **20 or more characters and 4 or more words** that fails either hard condition produces `POSSIBLE_SHORT_EXACT_DUPLICATE`; it is not auto-collapsed without a reviewed dependency or another hard identity. Canonical unit URLs, URL-derived native identities, repeated private provenance, and explicit reposts also collapse. The auditor completes all hard and transitive unions before selecting short-exact work. A match already in one final group does not warn. Each short-text class with any unresolved final-group pair emits exactly one deterministic pair warning per run; add that pair's decision and rerun until all pairs are reviewed. Warning paths and generated artifacts remain deterministic under ledger-row permutations. Generated audit and findings artifacts disclose each collapsed group's origin, members, and reasons.

The optional fuzzy scan considers records with at least 30 tokens using five-token shingles and a 90% length/similarity threshold. It has deterministic ceilings of 100,000 candidate pairs, 5,000,000 shingle lookups, and 200,000 stored shingle digests. Exceeding any ceiling produces `FUZZY_SCAN_SKIPPED`, a strict warning requiring external blocking/review instead of unbounded work.

## `signal-catalog.json`

```json
{
  "schema_version": "1.0",
  "signals": [
    {
      "id": "sig-001",
      "name": "Auditable community research",
      "hypothesis": "Coding-agent users need community research that preserves sources and tests counterevidence.",
      "decision_relevance": "A portable skill could make product-demand research reproducible.",
      "support_citations": ["src-001"],
      "counter_citations": [],
      "claimed_level": "anecdotal",
      "wtp_statement": null,
      "wtp_citations": [],
      "alternative_explanations": ["The request may reflect one community rather than broad demand."],
      "disconfirming_evidence_needed": "Independent costly behavior in other communities."
    }
  ]
}
```

Allowed levels: `unsupported`, `anecdotal`, `recurring`, `well-corroborated`. The audit rejects a claim above the calculated ceiling and permits a deliberate underclaim. When no eligible support exists, an all-`unsupported` catalog does not itself emit `NO_ELIGIBLE_SUPPORT`; a positive-level claim still triggers the no-support and claim-ceiling gates. Support and counter citations must have matching stances and reconcile with each source's `signal_ids`. A duplicate group cannot appear on both sides of one signal.

A non-null `wtp_statement` and nonempty `wtp_citations` must appear together. `wtp_statement` records the researcher's analytical rationale; it is non-scoring and is not repeated in generated findings. The public WTP section is generated only from the computed status/basis and exact cited source excerpts. Every WTP citation must have a known author, promotion `no`, be a support citation, and be tagged `purchase_intent` or `observed_payment`.

## `research-notes.json`

```json
{
  "schema_version": "1.0",
  "observations": [{"text":"I need a research skill that verifies every source","source_ids":["src-001"]}],
  "inferences": [{"text":"Auditability may be a stronger wedge than broad search.","signal_ids":["sig-001"]}],
  "recommendation": {"text":"Prototype the auditable workflow.","signal_ids":["sig-001"],"caveats":["Observed demand is not a representative survey."]},
  "next_tests": ["Compare decisions with and without duplicate collapse."],
  "coverage_notes": ["Search ranking was opaque and results were truncated."],
  "stop_reason": "Declared coverage reached."
}
```

Each observation cites exactly one public source and its ledger text must exactly equal that source's literal `excerpt`. Put paraphrase or cross-source synthesis in an inference. Inferences and recommendations cite signal IDs. Notes never increase scores and remain labeled as researcher interpretation. Markdown rendering escapes syntax and NFKC/whitespace-normalizes display text, so inspect the ledger for byte-level quote binding.

The strings created by `init` for `recommendation.text` and `stop_reason` are explicit incomplete-research sentinels; leaving either one fails validation. `next_tests` must be nonempty. Replace all three with an evidence-bound conclusion, the actual stopping rationale, and a concrete next test even when the catalog remains empty.

## Generated `artifacts/`

- `audit.json`: stable issue codes, coverage, signal metrics, `source_groups_after_collapse`, `collapsed_duplicate_groups`, duplicate-group details, tool version, semantic input fingerprint, and output hashes. It is written last as the success commit marker.
- `signals.csv`: ranked metrics with spreadsheet-formula protection.
- `findings.md`: deterministic report with short public excerpts, excluded promotional context, counterevidence, full scope/query disclosure, limitations, and fingerprint. Private excerpts are withheld while their opaque record locator and caller-declared source-file SHA-256 are shown; the report states that the file/digest relationship is not authenticated offline.

The live directory must be the exact three regular-file set above, with each artifact no larger than 32 MiB. Each CSV, Markdown, and JSON renderer counts UTF-8 bytes as content is appended and aborts before crossing the ceiling, including during citation expansion; the limit is not deferred until after an unbounded artifact is materialized. The complete generation is staged and switched under one random transaction token. A staging failure leaves the prior set untouched; a failed switch that leaves the live path absent restores the exact backup. Any unexpected file, directory, symlink, Windows junction/reparse point, or other entry in `artifacts/` is refused rather than deleted.

Every `build` boundedly scans for interrupted transaction state while holding an exclusive, nonblocking cross-process study lock (`msvcrt.locking` on Windows and `fcntl.flock` on POSIX). `build` holds the lock across recovery, analysis, staging, swap, validation, and cleanup; standalone recovery holds the same lock for its full operation. A concurrent second tool writer fails fast without touching the active transaction. The persistent `.csr-build.lock` must be a stable regular non-link/reparse file inside the study directory and at most one byte long. Only stage/backup names whose token is exactly 32 lowercase hexadecimal characters are cleanup candidates; lookalike prefix directories remain untouched. A backup must contain the exact three regular artifacts; a disposable stage may contain only artifact names plus at most one `.csr-*.tmp`. With live artifacts, the live set and backups must be exact, stages must be disposable, validated nonoverlapping transaction directories are removed, and a same-token stage/backup pair is refused. Without live artifacts, stage-only states are validated and removed; exactly one exact backup is restored, its same-token stage must be exact if present, and other stages may be disposable. Multiple restore backups, unexpected contents, and symlinks, Windows junctions, or other reparse paths are refused.

The completed stage is exact-set and reparse-safe validated immediately before rename. The installed live set is validated immediately after install and revalidated before its exact backup is validated and deleted. Unexpected or replaced stage paths are preserved for manual inspection; a suspect installed live set and its backup are likewise preserved instead of being guessed away. `audit` remains read-only, does not take the writer lock or mutate recovery state, regenerates expected bytes in memory, bounds each artifact read to expected length plus one byte, and rejects missing, stale, CRLF-mutated, oversized, edited, or extra entries.

The public semantic fingerprint redacts `title`, `captured_text`, `excerpt`, and `notes` for every ledger row whose `visibility` is not exactly `public`, including structurally invalid rows with malformed or missing intended-private visibility. It remains bound to every other field; for valid supplied-private rows that includes `source_file_sha256`, `record_ref`, classifications, and citations, while all public inputs remain fully bound. Therefore a redacted-text-only edit with unchanged structured provenance does not change the public fingerprint.

Separately, every free-text field that can flow into public artifacts is checked for ASCII-shaped email patterns, and non-URL fields are checked for ASCII-shaped phone patterns. The regex character/digit/boundary classes are explicit rather than runtime Unicode `\w`, `\d`, `\b`, or case folding. For private title/text/excerpt/notes, the scan indexes every four-token window; a one-to-three-token field of at least eight normalized characters is indexed as a whole. Alphanumeric private identifier components of at least 12 characters are additionally compared with output IDs, opaque record references, and public URLs. The scan refuses more than 200,000 distinct digests. It is intentionally conservative and bounded: generic phrases can produce false positives, while Unicode-formatted contact data, paraphrases, alternate encodings, short secrets, addresses, and unrecognized identifier forms can evade it. It does not replace human review before publication.
