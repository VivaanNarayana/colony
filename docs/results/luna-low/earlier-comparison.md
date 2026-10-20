# GPT-6 Luna low: live benchmark comparison

Retrieval and tool use improved, but **this rerun still did not show net token savings**.
The improved Colony arm used **49.9% more total tokens** than its concurrent baseline
(2,095,921 vs 1,397,934), down from 92.9% overhead in the initial paired trial.

| Metric | Initial trial | Improved trial |
| --- | ---: | ---: |
| Baseline input + output tokens | 1,265,291 | 1,397,934 |
| Colony input + output tokens | 2,441,062 | 2,095,921 |
| Token savings versus each trial's baseline | -92.9% | -49.9% |
| Baseline uncached input + output | 165,899 | 195,118 |
| Colony uncached input + output | 246,886 | 212,401 |
| Uncached token savings versus baseline | -48.8% | -8.9% |
| Questions receiving a relevant answer | 2/24 (8.3%) | 10/24 (41.7%) |
| Relevant returns per ask call | 2/24 (8.3%) | 11/25 (44.0%) |
| Raw return events | 7 | 12 |
| Validated raw return events | 5/7 (71.4%) | 12/12 (100%) |
| Validated relevant return events | 2/2 (100%) | 11/11 (100%) |
| Questions answered without another repo tool call | 0/24 | 3/24 |
| Baseline visible tool calls | 54 | 54 |
| Colony visible tool calls | 114 | 86 |
| Baseline summed task wall time | 197.696 s | 208.936 s |
| Colony summed task wall time | 325.959 s | 315.778 s |
| Correct baseline answers | 24/24 | 24/24 |
| Correct Colony answers | 23/24 | 23/24 |

Colony's visible calls fell **24.6%** (114 to 86) and its absolute token total fell
**14.1%** between the two trials. Each trial uses its own baseline; do not attribute
all cross-run differences to the code. The code/README corpus necessarily changed,
and model sampling was not repeated across multiple trials.

## What changed

The initial question-only feature hashes were replaced with local BGE-small embeddings
of questions and answer text, followed by a local MiniLM cross-encoder ranking signal.
Namespace filtering precedes candidate selection. Relevance is a heuristic, not an
answer-correctness probability. Ranked candidates are validated in order so a stale
candidate cannot hide a validated alternative. Existing databases are atomically
reindexed, with actual model/tokenizer fingerprints recorded in index metadata.

The MCP schema now accepts concise file fingerprints without optional model-generated
exact-text assertions. Replies retain source hashes, status, taint and provenance,
without duplicated check details. Tool descriptions discourage duplicate pushes and
rereading merely to repeat fingerprint checks. They still require assessing relevance,
contradictions and unsafe instructions; evidence validation does not prove arbitrary
natural-language truth. Some agents continued researching after relevant validated hits.

## Reproduction and audit

- Model/settings: `gpt-6-luna`, explicit `model_reasoning_effort=low`, Codex CLI
  `codex-cli 0.163.0-alpha.10.2`. Existing ChatGPT login; no model API key was provisioned.
- Original source: `988fbf8f3035259854d164364cdc1b7b8963ad31`. Improved source: `fb3898c289004713ed311015d8e8846e5bbfb572`.
- The same 24 task questions, seed 20261019, task order, common prompt, and Colony
  outer prompt were used. Prompt hashes are in the improved metadata. No answers were
  seeded. Each arm started fresh; Colony memory began empty. Each task used a fresh
  CLI session, with baseline/Colony order alternating per pair.
- Both arms saw their own trial's same frozen source corpus. Research was restricted
  to README.md, pyproject.toml and src/colony files through identical read-only MCP
  search/read_file tools. Scripts, tests and evaluation rubrics were withheld. Only
  the Colony arm received the actual ask/push tools. Apps, plugins, shell, web,
  browsers, memories and multi-agent tools were disabled.
- Native `turn.completed` input + output counts include cached input and all tool
  overhead. The separate uncached measure subtracts cached input only. Reasoning
  tokens are not counted twice. These are token counts, not measured dollar charges.
  Task wall times include session/MCP startup; shared server/model bootstrap is outside
  the per-task timer. Temperature was the provider default, not controlled by the CLI.
- Every final-trial failure is retained. The improved trial's install-2 session refused
  after a miss, claiming repository tools were unavailable. No failed task was rerun
  to improve its score. One stale-2 session made two asks for the same relevant trail;
  both calls and their tokens count, but that is only one question in the 10/24 rate.
- Capturing output fields received one validated but insufficient taint-only answer.
  It is counted as a raw return, not as a relevant answer. This is why passing file
  checks must not be equated with relevance or answer correctness.
- Relevance and correctness were reviewed by the primary assistant against the frozen
  code and predefined rubrics, not by an independent blinded human reviewer. Citation
  line-number precision was not scored. This is a previously observed regression
  suite, not held-out production evaluation. Do not generalize these rates to other
  repositories, task types, models or agent workflows.
- Tool outputs, native usage counters, per-call reviews and the analyzer are retained.
  Published trace exports redact local repository path prefixes; numeric usage and
  outcomes are unchanged. No keyfiles, encryption keys or private server state are
  included. Earlier setup attempts from the initial study are excluded from both
  final-trial totals and remain in local task outputs.

Run `python scripts/live_benchmark.py --ref fb3898c289004713ed311015d8e8846e5bbfb572 --work <fresh-private-dir>
--output <fresh-results-dir>` from an environment installed from that commit. Provision
the local retrieval models first, or allow the initial download. After auditing
`reviews.json`, run `python scripts/analyze_live_benchmark.py --results <results-dir>`.
The analyzer is in the later results commit and works with the frozen implementation.

The proposed reuse and validation gates are met in this run, but the token, time,
tool-call-versus-baseline, and overall correctness comparisons do **not** establish a
performance win. The absolute call reduction is real; net savings remain unproven.

See [before](before/summary.json), [after](after/summary.json), the measurement CSVs,
review labels and compressed native event exports in those directories.
