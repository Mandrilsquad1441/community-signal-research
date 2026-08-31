# Source playbooks

Use native browsing, search, official APIs/connectors, or user-supplied exports. The included helper deliberately makes no network calls. Record reviewed direct permalinks only: never retain access tokens, session/signature parameters (including compact/camel keys such as `token`, `session`, `privateKey`, `secretKey`, `signingKey`, or `keyPairId`), URL userinfo, contact data, or private/local hosts. Do not use the apex or descendants of `home.arpa`, `internal`, `lan`, `local`, `localdomain`, or `localhost`. Raw-Unicode hosts are refused; use an explicit ASCII IDNA A-label when needed. Native community hosts reject nondefault ports.

## Reddit

Search across relevant subreddits and, when possible, compare relevance, new, and top results. Capture the post or individual comment permalink in `url` and the containing post permalink in `thread_url`, not a search-result URL. Use the native `reddit:t3_<post-id>` thread ID; comments use `reddit:t1_<comment-id>` as their unit ID. The auditor derives these identities from positive ASCII base-36 URL IDs after case and leading-zero normalization. Treat cross-posts, copied launch posts, and repeated product promotion as dependent evidence.

Useful query language includes:

- `"is there a"`, `"wish there was"`, `"need a way"`;
- `"doesn't work"`, `"keeps"`, `"gave up"`, `"waste of time"`;
- `"manual"`, `"spreadsheet"`, `"script I wrote"`, `"switch between"`;
- `"paid"`, `"would buy"`, `"budget"`, `"too expensive"`;
- counterqueries such as `"already solved"`, `"works fine"`, `"not needed"`, and named alternatives.

Record the displayed score and comment count only with a snapshot timestamp. They do not affect ranking.

## GitHub Issues and Discussions

Prefer original issues, discussion posts, and individual comments. Only exact host `github.com` receives native GitHub handling; subdomains are generic sources. Use canonical `/owner/repository/issues/<number>` or `/owner/repository/discussions/<number>` URLs; an individual comment's fragment must identify that comment and its `thread_url` must be the containing root. Use the URL-derived GitHub root/comment identities. Separate maintainers, product vendors, bots, and users in notes; mark promotional or conflicted evidence conservatively.

Search open and closed items. A closed issue may show that the need is already served; an unresolved, repeatedly referenced issue may show persistence. Do not count reactions as distinct author evidence unless those accounts also provide inspectable statements or behavior.

## Hacker News

Capture the exact `news.ycombinator.com/item?id=<positive ASCII integer>` permalink for the story or comment and the root story permalink in `thread_url`; the auditor normalizes leading zeros, derives both `hackernews:item:<id>` identities, requires a story URL to equal its thread URL, and rejects a comment whose item ID equals its declared root. HN comment URLs still do not encode the root story, so manually verify that declared relationship. HN comments often quote parent comments; remove quoted text before checking near-duplicate content when practical. Sample more than the top-ranked branch because ranking creates visibility bias.

## Forums and reviews

Use stable direct links and identify whether one review has been syndicated. Generic URL identity preserves repeated/trailing path slashes, query order, and fragments; use the helper's canonical form rather than guessing that these server-defined distinctions are interchangeable. Platform ratings alone are not atomic evidence. Capture the text that describes the job, pain, workaround, adoption, or counterexample.

## Supplied CSV, JSON, transcripts, or exports

Document provenance and the transformation into ledger records. Use `platform: "export"`, `source_type: "export_record"`, `capture_method: "export"`, `visibility: "supplied_private"`, null URLs, an opaque non-personal `record_ref`, and the authorized source file's caller-declared SHA-256. The helper cannot authenticate that digest against a file it never receives. Redact personal information and state that remote-source verification was unavailable. Freeze a public-only draft—including summaries, limitations, and next tests—while private text and record metadata are `[SEALED]`. Then reduce private rows to a structured patch of permitted provenance and controlled support/counter/WTP membership. Apply it only to structured IDs/counts/labels, opaque citations, and the applicable exact fact-free templates; never edit prose afterward. Outside exact templates, never make private evidence the subject of a descriptive clause, combine public/private attribution for a fact, or say what a private source indicates, contains, lacks, does, or does not supply. Run the deletion gate: any changed non-template wording is a privacy failure. A safe complete form is "One supplied-private source meets the explicit purchase-intent criterion; record-specific details withheld." Generated findings withhold private excerpts and the listed private metadata, while a bounded hashed overlap scan checks public-output text, URLs, IDs, and locators for exact or near-exact leakage; that scan does not prove semantic non-disclosure, so review generated findings at the response boundary before publication.

## Search-quality checks

- Run synonymous and opposing terms.
- Record pagination and truncation.
- Record at least one viewed page for every query, screen at least one result whenever results are seen, and do not mark countersearch complete from a truncated execution.
- Include sources with low engagement when relevant.
- Inspect at least one older and one recent result in a standard study.
- Avoid counting summaries of the same underlying event as distinct evidence.
- Name channels that could not be accessed.
