# Data contracts

The helper reads UTF-8 JSON and JSONL, rejects duplicate object keys and unknown fields, and writes deterministic UTF-8/LF artifacts. IDs use `qry-*`, `src-*`, and `sig-*` prefixes. Dates use ISO 8601; timestamps require a timezone.

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

`intent` is `support`, `counter`, or `neutral`. Counts are non-negative integers and screened results cannot exceed seen results. A query with included source IDs must report at least one seen and screened result. A query can include several atomic comments from one screened thread, so inclusions may exceed screened results. Every source/query inclusion must reconcile in both directions. Link a counterquery to every signal it attempts to disconfirm, even if it returns no included sources.

## `source-ledger.jsonl`

Public source:

```json
{"schema_version":"1.0","id":"src-001","platform":"reddit","community":"r/codex","source_type":"post","url":"https://www.reddit.com/r/codex/comments/example/research/","record_ref":null,"visibility":"public","capture_method":"browser","source_file_sha256":null,"thread_url":"https://www.reddit.com/r/codex/comments/example/research/","unit_id":"reddit:t3_example","thread_id":"reddit:t3_example","published_at":"2026-05-01T10:00:00Z","collected_at":"2026-08-30T12:05:00Z","author_key":"author:7196b9a20d5b8a3e","language":"en","source_status":"available","title":"Research workflow","captured_text":"I need a research skill that verifies every source and checks counterexamples.","excerpt":"I need a research skill that verifies every source","stance":"support","evidence_types":["desired_outcome"],"promotional":"no","repost_of":null,"query_ids":["qry-001"],"signal_ids":["sig-001"],"engagement":{"score":12,"comments":5,"snapshot_at":"2026-08-30T12:05:00Z"},"duplicate_reviews":[],"notes":""}
```

Supplied private export:

```json
{"schema_version":"1.0","id":"src-002","platform":"export","community":"authorized-customer-export","source_type":"export_record","url":null,"record_ref":"survey.csv:row-17","visibility":"supplied_private","capture_method":"export","source_file_sha256":"sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","thread_url":null,"unit_id":"export:row-17","thread_id":"export:response-17","published_at":"2026-06-02T09:00:00Z","collected_at":"2026-08-30T12:10:00Z","author_key":"unknown","language":"en","source_status":"available","title":"","captured_text":"The authorized supplied response text.","excerpt":"The authorized supplied response text.","stance":"support","evidence_types":["problem"],"promotional":"no","repost_of":null,"query_ids":["qry-002"],"signal_ids":["sig-001"],"engagement":{"score":null,"comments":null,"snapshot_at":"2026-08-30T12:10:00Z"},"duplicate_reviews":[],"notes":"Private excerpt is never rendered in generated findings."}
```

For public records, `url` and `thread_url` are required canonical HTTP(S) URLs on publicly routable hosts; `record_ref` and `source_file_sha256` are null. Reddit, GitHub, and Hacker News platform labels must match their native hosts. Reddit post/comment IDs and thread IDs must match the native URL, so invented IDs cannot manufacture independence. For supplied-private records, URLs are null, `record_ref` is an opaque 1-256 character locator without personal data, and the authorized source file's SHA-256 is required. Never fabricate a URL for a private record.

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

Allowed promotion values are `yes`, `no`, and `unclear`. Only `no` is eligible for positive counts, labels, scoring, or WTP. `unclear` adds no positive evidence and incurs an uncertainty penalty. `yes` is display-only.

`author_key` is exactly `unknown` or `author:` plus 16-64 lowercase hexadecimal characters. Use `author-key --study-dir`; do not publish `.author-key`. Keys are pseudonyms for observed accounts, not proof of unique people or anonymity.

`excerpt` must be a literal substring of `captured_text`, at most 25 whitespace-delimited words, and at most 500 characters. C0/C1 control characters are rejected from source text and other user-controlled strings. `repost_of` points directly to an origin whose own value is null; chains, self-links, missing targets, and cycles fail. Duplicate signal sources should cite the group origin.

Fuzzy candidate review is machine-readable:

```json
"duplicate_reviews": [{"other_source_id":"src-009","decision":"independent","reason":"Different authors describe separate implementations with similar boilerplate."}]
```

Allowed decisions are `same_source` and `independent`. `same_source` collapses the pair. `independent` clears that pair's fuzzy warning. Exact substantive text, canonical unit URLs, native platform/unit IDs, and explicit reposts are collapsed automatically.

The optional fuzzy scan has a deterministic 100,000-pair work budget. Larger long-text sets receive a strict warning and require external blocking or review instead of triggering an unbounded quadratic scan.

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

Allowed levels: `unsupported`, `anecdotal`, `recurring`, `well-corroborated`. The audit rejects a claim above the calculated ceiling and permits a deliberate underclaim. Support and counter citations must have matching stances and reconcile with each source's `signal_ids`. A duplicate group cannot appear on both sides of one signal.

A non-null `wtp_statement` and nonempty `wtp_citations` must appear together. Every WTP citation must be promotion `no`, a support citation, and tagged `purchase_intent` or `observed_payment`.

## `research-notes.json`

```json
{
  "schema_version": "1.0",
  "observations": [{"text":"Requests emphasize verification rather than faster summarization.","source_ids":["src-001"]}],
  "inferences": [{"text":"Auditability may be a stronger wedge than broad search.","signal_ids":["sig-001"]}],
  "recommendation": {"text":"Prototype the auditable workflow.","signal_ids":["sig-001"],"caveats":["Observed demand is not a representative survey."]},
  "next_tests": ["Compare decisions with and without duplicate collapse."],
  "coverage_notes": ["Search ranking was opaque and results were truncated."],
  "stop_reason": "Declared coverage reached."
}
```

Observations cite source IDs. Inferences and recommendations cite signal IDs. Notes never increase scores and remain labeled as researcher interpretation.

## Generated `artifacts/`

- `audit.json`: stable issue codes, coverage, signal metrics, tool version, semantic input fingerprint, and output hashes. It is written last as the success commit marker.
- `signals.csv`: ranked metrics with spreadsheet-formula protection.
- `findings.md`: deterministic report with short public excerpts, excluded promotional context, counterevidence, full scope/query disclosure, limitations, and fingerprint. Private excerpts are withheld while their opaque record locator and authorized-file SHA-256 are shown.

The complete three-file generation is staged and switched as one set. A handled write failure restores the prior committed artifacts, and an unexpected entry in `artifacts/` is refused rather than deleted. If a process or machine stops in the narrow interval between directory renames, the next `build` restores the single validated `.csr-artifacts-backup-*` generation before continuing; `audit` remains read-only and will fail while the live directory is absent. `audit` regenerates the expected bytes in memory and rejects missing, stale, edited, or extra artifact files.

The public semantic fingerprint redacts supplied-private `title`, `captured_text`, `excerpt`, and `notes` fields to avoid dictionary-testing low-entropy responses. It remains bound to `source_file_sha256`, `record_ref`, classifications, citations, and all public inputs. Therefore a private-text-only edit with unchanged authorized-file hash and structured evidence does not change the public fingerprint.
