# Evidence-first packing and explicit budget boundaries

Adaptive packing now fits required source units and nominated complete-file
expansions before saved prose. It restores the complete saved answer only when
that answer fits without displacing the selected source text. It never truncates
the answer, drops required sources, or expands unrelated files to fill capacity.
Ordinary unprofiled packing retains its original ordering.

The opt-in context budget now defaults to 3,072 units: UTF-8 bytes unless an
encoding is explicitly selected. This applies to `ContextBudget()` and the CLI
`--context-budget` flag without a number. An explicit 2,048 remains 2,048;
existing generated configurations and historical evaluation protocols are not
rewritten. No-budget connections remain outside adaptive mode.

For a newly installed multi-file workflow, select the configuration explicitly:

```sh
colony --home /absolute/private/agent-home setup codex install --repo /absolute/project --ns team --context-budget 3072 --context-encoding utf8_bytes
```

The regression matrix checks 2,048, 3,072 and 4,096 bytes through real storage,
HTTP, client and MCP delivery with LF/CRLF and Unicode. Its four-file non-Git
bundle cannot fit completely at 2,048, but fits at 3,072. Git framing has a
separate cost; the matrix also checks complete delivery at 4,096 and bounded
behavior at lower allowances. The default is a moderate opt-in capacity choice,
not a promise that every bundle fits or a universal optimal budget.

Existing boundary regressions explicitly retain their original 2,048 allowance;
new tests cover the changed default separately. All source identities, trust,
lifecycle, required-unit and independent Git checks still apply. Oversized
required evidence fails closed. A public delivered span remains indivisible on
later repacking; no new trusted selection metadata crosses the wire. Every
lookup still makes one server ask, with no automatic retry or cap increase.

This response-packing policy imposes no limit on the user's answer. Necessary
research, requested detail, examples, citations and uncertainty remain available.
Saved prose may contain a useful interpretation and is retained when it fits
after evidence. This does not classify a stored interpretation as verified truth.

Host integration, publication behavior, retrieval/ranking and evaluator changes
are separate. No model evaluation is part of this change. Larger useful replies
may reduce follow-up research, but may also increase gross usage; the regression
matrix establishes delivery and accounting only. Historical costs and grades
remain attached to their original configurations and revisions.
