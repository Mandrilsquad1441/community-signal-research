# Source playbooks

Use native browsing, search, official APIs/connectors, or user-supplied exports. The included helper deliberately makes no network calls.

## Reddit

Search across relevant subreddits and, when possible, compare relevance, new, and top results. Capture the post or individual comment permalink, not a search-result URL. Treat cross-posts, copied launch posts, and repeated product promotion as dependent evidence.

Useful query language includes:

- `"is there a"`, `"wish there was"`, `"need a way"`;
- `"doesn't work"`, `"keeps"`, `"gave up"`, `"waste of time"`;
- `"manual"`, `"spreadsheet"`, `"script I wrote"`, `"switch between"`;
- `"paid"`, `"would buy"`, `"budget"`, `"too expensive"`;
- counterqueries such as `"already solved"`, `"works fine"`, `"not needed"`, and named alternatives.

Record the displayed score and comment count only with a snapshot timestamp. They do not affect ranking.

## GitHub Issues and Discussions

Prefer original issues, discussion posts, and individual comments. Record repository and issue/discussion identifiers. Separate maintainers, product vendors, bots, and users in notes; mark promotional or conflicted evidence conservatively.

Search open and closed items. A closed issue may show that the need is already served; an unresolved, repeatedly referenced issue may show persistence. Do not count reactions as distinct author evidence unless those accounts also provide inspectable statements or behavior.

## Hacker News

Capture the item permalink for the story or comment and a stable thread identifier. HN comments often quote parent comments; remove quoted text before checking near-duplicate content when practical. Sample more than the top-ranked branch because ranking creates visibility bias.

## Forums and reviews

Use stable direct links and identify whether one review has been syndicated. Platform ratings alone are not atomic evidence. Capture the text that describes the job, pain, workaround, adoption, or counterexample.

## Supplied CSV, JSON, transcripts, or exports

Document provenance and the transformation into ledger records. Use `platform: "export"`, `source_type: "export_record"`, `visibility: "supplied_private"`, null URLs, an opaque `record_ref`, and the authorized source file's SHA-256. Redact personal information and state that remote-source verification was unavailable. Generated findings withhold private excerpts.

## Search-quality checks

- Run synonymous and opposing terms.
- Record pagination and truncation.
- Include sources with low engagement when relevant.
- Inspect at least one older and one recent result in a standard study.
- Avoid counting summaries of the same underlying event as distinct evidence.
- Name channels that could not be accessed.
