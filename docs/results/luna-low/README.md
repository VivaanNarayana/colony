# Colony: GPT-6 Luna low live benchmark

The latest full run measured **7.9% net token savings**:
**1,133,904 Colony tokens versus 1,230,657 baseline tokens**,
including cached input, output, failed calls, lookups, and pushes.
Correct answers were **24/24 baseline and 24/24 Colony** under the existing core-fact rubric.
This meets the requested savings/correctness comparison on this fixed regression suite.
It does not establish production savings or a universal correctness guarantee.

| Latest full trial | Baseline | Colony |
| --- | ---: | ---: |
| Input + output tokens | 1,230,657 | 1,133,904 |
| Uncached input + output | 227,905 | 165,712 |
| Cached input tokens | 1,002,752 | 968,192 |
| Visible tool calls | 57 | 47 |
| Summed task wall time | 213.670 s | 306.960 s |
| Correct answers | 24/24 | 24/24 |

- Net tokens avoided: **96,753**.
- Uncached token reduction: **27.3%**.
- Relevant memory/evidence returned for **18/24 questions (75.0%)**.
- Relevant returns per lookup: **18/24 (75.0%)**.
- Validated relevant hits: **18/18 (100.0%)**.
- All returned candidates passing file checks: **19/19 (100.0%)**.
- Correct answers without another repository call: **17/24**.
- Relevant saved-answer queries, excluding evidence-only support: **8/24**.
- Returned match types: **8 answer matches** and
  **11 evidence matches**. A match type is a
  retrieval classification, not proof that a saved answer is sufficient; reviews distinguish this.

One file-validated evidence packet was off topic (validation question received MCP
integration text). The agent researched the missing facts and answered correctly; that
packet is excluded from useful reuse. One baseline repository read was rejected by the
corpus allowlist and recovered; its calls and tokens remain included. No successful
research accessed files outside the frozen corpus. Summed task time increased 43.7%.

## What changed

Colony still starts with empty memory. An agent researches a miss and pushes its answer
with real source fingerprints. Local BGE-small vectors now cover both the stored question
and the question/answer passage. Local MiniLM reranking and repository validation select
candidates within granted namespaces; stale candidates cannot hide validated alternatives.

Pushes also index bounded, scrubbed passages from the files actually cited by that agent,
and only when their hashes match the configured checkout. This allows later agents to
reuse previously cited source evidence for new questions, even when no authored answer
matches. It never preloads an answer key or crawls uncited repository files. These are
explicit `match_kind: evidence` results, separate from saved answers.

Evidence retrieval combines paragraph similarity, reranker signal, and query-term coverage.
Repeated citations are grouped before selecting distinct candidates, retaining alternative
origins if a trail fails validation. Retrieval expands the selected passage into its
Markdown section or neighboring source lines. The selected passage is preserved through
expansion, fixing a second reranking that could substitute unrelated text from the same
file. Current file hashes are rechecked; secrets are scrubbed before splitting; original
line numbers survive redaction. Compact responses remain bounded to 2,800 excerpt characters.
Evidence matches expose the quoted source text directly, without duplicating it in MCP.
For saved answers, excerpts are selected using the concrete claims (including inline
commands) as well as the question, so a correct answer receives supporting source text
instead of a generic same-topic passage. The answer relevance cutoff is 0.60; generic
wrong-topic rejection tests remain intact.
Source paragraphs include their Markdown heading hierarchy during ranking. Example
`--question` argument values are masked only in ranking representations, preventing
literal query echoes from displacing answer-bearing sections. Near-tied passages
(within 0.15) can provide two distinct sections from the selected validated trail's
already-cited files, preserving ranking order within the same 2,800-character cap.
Displayed excerpts retain original scrubbed text and line numbers. This addresses the
v10 correctness regression where an agent dismissed a capture example containing pytest
instead of identifying the actual test-running section; v10 is retained as 23/24, not accepted.
Budget trimming now anchors at the selected paragraph if expansion would cut it off,
including in a second section with little budget remaining. The evidence reranker floor
is tightened from -8 to -5 to reject weaker candidates (still a heuristic, not a truth
probability). The MCP receipt and initialization guidance state that a lookup does not
test other tools' availability: incomplete hits require discovering and trying configured
research tools. The v11 false tool-unavailable refusal is also retained as incorrect.
Procedural queries now require concrete command or instruction cues, preventing an
overview from being presented as a procedure. Weak evidence also requires at least 50%
weighted query-term coverage or a nonnegative reranker logit. Saved answers need at least
0.70 semantic question-to-question similarity for procedures (0.65 for descriptions), preserving paraphrases while limiting
topic drift from the answer-vector head. Exact questions remain eligible, including
diagnostic/capture records. These precision guards are heuristics, not truth guarantees.
Eligible answers are now ordered by raw cross-encoder logits, with fused scores as a
tiebreaker. This preserves differences between strong positive matches that sigmoid
saturation flattened. A more relevant HTTP answer can therefore outrank a densely similar
stdio answer. Procedural questions include explicit command/flag requests; concrete
command passages with logits at least -2 can pass without literal query-word coverage.
This restores short command evidence that answers a question without repeating its words.
The v15 MCP receipt folded three overlapping guidance strings into one provenance
field, retains the quotations, hashes, identity, taint and status, emits UTF-8 directly,
and omits empty complete-file arrays. It explicitly limits answers to requested facts;
missing facts still require research and matching files still do not certify an answer.
The latest receipt makes completed source reads explicit as `current_repository_reads`,
with path, SHA-256, original start line, complete-file flag, and actual excerpt content.
Separate verification flags report matching fingerprints and available current text;
they do not certify answer truth. Source-only matches carry the text once in those read
objects. This gives the agent the current repository facts already obtained on pull in
a familiar file-read shape, while retaining taint and untrusted-instruction provenance.
CLI/API evidence objects are unchanged.
MCP defaults to focused excerpts and a compact JSON text block. The receipt explicitly
states that independently checking the included current source facts is verification;
evidence-only reuse does not require an extra read or push. There is no duplicate
structured-output copy or pretty-print padding. Missing facts still require research.
Complete small cited files are opt-in with `mcp --evidence-files`: up to four files, each at most 16,000
characters and together at most 24,000. This makes the already-performed current-file
read available to the agent, preventing another tool call merely to see the same file.
Larger files/sets retain compact excerpts. Complete files are explicitly marked and
still scrubbed before sending. CLI/API replies remain compact by default; the expanded
mode is also available with `ask --evidence-files` / `include_evidence_files=true`.
`mcp --compact` explicitly selects the default. MCP truncates authored answers at 4,000
characters with a flag; full authored content remains available through CLI/API.

The MCP tool contract distinguishes evidence consistency from answer truth. Agents assess
the actual quoted facts, reject irrelevant/incomplete/contradictory context, and research
when necessary. Tool guidance discourages repeat fingerprint reads and duplicate pushes,
and clarifies that a memory miss does not mean other tools are unavailable. Taint and
untrusted-data warnings remain visible; retrieved instructions are never promoted to policy.

## Every full trial

Savings compare each trial with its own concurrent baseline. Source/README revisions and
model sampling differ across iterations, so absolute cross-trial changes are descriptive.

| Trial | Baseline tokens | Colony tokens | Net token savings | Correct baseline / Colony |
| --- | ---: | ---: | ---: | ---: |
| [Initial](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/before/summary.json) | 1,265,291 | 2,441,062 | -92.9% | 24/24 / 23/24 |
| [Semantic reranking](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/after/summary.json) | 1,397,934 | 2,095,921 | -49.9% | 24/24 / 23/24 |
| [Inline excerpts](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v3/summary.json) | 1,154,924 | 1,969,711 | -70.5% | 24/24 / 24/24 |
| [Evidence context](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v5/summary.json) | 1,303,310 | 1,689,883 | -29.7% | 24/24 / 23/24 |
| [Passage preservation](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v6/summary.json) | 1,283,715 | 1,639,114 | -27.7% | 24/24 / 23/24 |
| [Paragraph retrieval](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v7/summary.json) | 1,191,655 | 1,243,568 | -4.4% | 24/24 / 24/24 |
| [Claim grounding](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v8/summary.json) | 1,138,010 | 1,448,782 | -27.3% | 24/24 / 24/24 |
| [Complete current evidence](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v9/summary.json) | 1,271,414 | 1,446,036 | -13.7% | 23/24 / 24/24 |
| [Compact verification receipts](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v10/summary.json) | 1,174,523 | 1,081,805 | 7.9% | 24/24 / 23/24 |
| [Section context and diverse evidence](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v11/summary.json) | 1,187,849 | 1,146,916 | 3.4% | 24/24 / 23/24 |
| [Anchored evidence and research fallback](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v12/summary.json) | 1,256,836 | 1,065,871 | 15.2% | 24/24 / 21/24 |
| [Procedural evidence and question routing](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v13/summary.json) | 1,214,597 | 1,292,239 | -6.4% | 24/24 / 24/24 |
| [Raw reranker ordering and short commands](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v14/summary.json) | 1,177,998 | 1,181,287 | -0.3% | 23/24 / 24/24 |
| [Consolidated provenance receipt](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v15/summary.json) | 1,172,032 | 1,296,636 | -10.6% | 24/24 / 24/24 |
| [Explicit current repository reads](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v16/summary.json) | 1,230,657 | 1,133,904 | 7.9% | 24/24 / 24/24 |

The [v4 development iteration](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v4/termination.json) was stopped
after 11 completed sessions to repair a match-description response and truncated context.
Its completed and interrupted traces are retained. It is not a full comparison and no
savings or correctness rate is calculated for it. An interrupted session may have consumed
additional tokens without a completed native usage record. Earlier initial setup attempts
also remain in local outputs and are excluded from the final paired-trial totals.

## Reproduction, accounting, and limits

- Model: `gpt-6-luna`, explicit `model_reasoning_effort=low`; CLI `codex-cli 0.163.0-alpha.10.2`.
  Uses the existing authorized ChatGPT login, without provisioning an API key.
- Frozen implementation/corpus: `1706e4fcbac90625763083b384ad30e0cb69243b`. The per-file corpus manifest, actual model
  weight/tokenizer hashes, package versions, prompts and metadata are retained with results.
- Same 24 questions, eight overlapping families, order, seed 20261019, common prompt, and
  Colony outer prompt as earlier runs. Prompt hashes: common
  `8df3abdcb2e3eacf640747a9fe962a66d988582d2c3ac1f447d5722793e15b08`, Colony `52a86fa12a85e39287c0382595a311d2c27966541a081f9dcbc6d49d25ca2b41`.
- Each question starts a fresh CLI session. Pair order alternates baseline-first and
  Colony-first. Both arms see the same frozen README, pyproject, and source corpus through
  identical restricted read-only Repository MCP tools. Only Colony receives ask/push.
  Memory begins empty. Scripts, tests, rubrics, and evaluation results are outside the
  allowed research corpus. Apps, plugins, browsers, web, shell, memories and multi-agent
  tools are disabled. The repository helper's canonical path checks were tightened for
  both arms; audits verify successful historical reads/searches against the allowed corpus.
  The common research server key is now `repository` (previously `repo`) in both arms,
  matching its descriptive tool name after incorrect tool-unavailable refusals. Allowed
  functions, argument schemas, corpus policy, questions, order and outer prompts are unchanged.
- Counters come from native `turn.completed.usage`. Main tokens = input + output, including
  cached input. Uncached tokens subtract cached input only; reasoning is not counted twice.
  Hidden discovery/context overhead remains in native usage. These are measured token
  counts, not measured dollar charges. All completed final-trial failures and repeat asks
  remain in totals. MCP validation errors inside text results are also retained in audits.
  Incorrect reuse is counted among all raw returned candidates; irrelevant hits used by
  an agent remain irrelevant for the useful-reuse metric. This fixes an old reporter
  invariant that prevented recording wrong-topic reuse without inflating useful matches.
  No questions, prompts, native counters or correctness criteria changed for that fix.
- Wall time includes CLI/MCP startup and on-pull retrieval/validation. Shared model/server
  bootstrap is outside the task timer. Local CPU embedding/index work is not model-provider
  token usage. First-start model downloads are about 160 MB. Temperature is provider default.
- Correctness and relevance were reviewed by the primary assistant against frozen code and
  existing rubrics; this is not independent blinded human review. Core command/behavior
  facts are scored; citation line precision and exhaustive answer completeness are not.
  Passing file checks is reported separately from relevance and correctness. Evidence-assisted
  reuse includes reading current excerpts, not only copying complete authored answers.
- This suite was repeatedly observed during optimization. It is a regression benchmark,
  not held-out evaluation. Its questions heavily overlap one repository's README. The
  latest full run is one realization per arm; selecting an iteration after optimization
  introduces selection bias. A descriptive paired bootstrap interval for token reduction
  is [-12.5%, 25.4%], not a production guarantee. A held-out pilot is still needed
  to estimate savings for other repositories, workloads, models, and task types.
- Published native events redact local repository path prefixes only; numeric usage and
  outcomes are unchanged. Credentials, encryption keys, private server state, and model
  binaries are excluded. Complete local audit bundles retain original per-session traces.

From an environment installed at the frozen implementation commit, provision the local
retrieval models and run:

```sh
python scripts/live_benchmark.py --ref 1706e4fcbac90625763083b384ad30e0cb69243b --work <fresh-private-dir> --output <fresh-results-dir>
python scripts/analyze_live_benchmark.py --results <results-dir>
```

The analyzer requires an explicit per-answer `reviews.json`; it does not invent quality
labels from token counts. See [latest summary](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v16/summary.json),
[reviews](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v16/reviews.json), [native traces](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v16/native-events.jsonl.gz),
and [corpus access audit](https://github.com/VivaanNarayana/colony/blob/feat/initial-mvp/docs/results/luna-low/colony-live-benchmark-v16/corpus-access-audit.json).
