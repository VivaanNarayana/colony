# Lookup optimization: strict gate status

The PR remains draft. Attempted-call and source-provenance compliance are blocking.
The latest complete 48-session trial failed: main omitted punctuation from one
lookup question, and the feature transposed two characters in a pushed source hash.
Both failures and all native costs are retained. Neither failure is advisory.

The new shared research adapter reports remaining calls and supplies an exact
`source` object for `push.sources`, with instructions to reuse it verbatim and
preserve question punctuation. Both arms receive identical tooling. The six-call
limit, original questions/prompts/rubrics, and exact fingerprint checks remain.

Fresh local timing: 77.971s main versus 19.700s feature (74.7% lower), with all
72 paired retrieval responses matching. The runtime code is unchanged by the
research-tool feedback fixes. Tests: 86 passed, one host symlink skip; Ruff clean.

The latest raw live totals in comparison.json are diagnostic only because its
execution/provenance audit failed. A new complete trial is required before merge
review. Other features remain paused. Earlier failed trials remain in history/.
