# Tests-only correctness and protocol checkpoint 46

Source `c181654bcf3422075310386b85b78c9ae80babbd` on `feat/benchmark-independent-evaluation`. Full suite: **285 passed, one existing Windows symlink skip**, 144.977s JUnit duration. Ruff lint and whitespace checks pass. Final focused suite: 111 passed. Eight new final regression cases; every existing test and benchmark script is unchanged.

## Final changes

Descriptive evidence lookups retain their original relevance guards, scoring, candidate pools, models and cutoffs. When a selected Markdown detail excerpt lacks its section introduction, the existing `context_window` can supply that context from the same freshly fingerprinted, scrubbed source. A containing section window replaces the detail once; otherwise an unused second slot may carry a nonoverlapping introduction. A second ranked passage is never evicted. The original limits remain two excerpts, 2,400 characters each and 2,800 combined. Procedural and complete-file paths retain their original behavior.

This preserves definitions and qualifications that a metadata paragraph omits. Two long-document cases use unrelated adapter operations and renderer formats, require the requested fact somewhere in actual current evidence, reject different unsupported attributes, reject denied scope and reject changed files. Three context cases check qualifications/redaction/line identity, preservation of a second ranked source and retention of the selected fact when the introduction will not fit.

The MCP schema explicitly publishes exactly 64 characters in addition to the unchanged lowercase-hex pattern. Field descriptions, session policy and research/save guidance require copying each path/hash pair verbatim from the same successful new research read and comparing the whole hash before push. The accepted proof set is unchanged: no hash repair, recomputation, padding, normalization or ask-hash substitution. Existing malformed/advanced-source rejection tests remain unchanged and pass.

Validated-hit guidance now explicitly checks every requested fact: research a missing fact before answering, and reuse sufficient current reads without confirmation reads or a push. Two receipt cases test the same conditional contract for sufficient and metadata-only text, without pretending the server has certified answer completeness. A wire-schema case checks copy guidance and deletion-of-one-hash-character rejection before the client is called.

These schema/instruction regressions **do not prove live model adherence** or correct future fingerprints. The server cannot observe an external tool's research history. The unchanged analyzer must still verify every actual attempted proof/save in both arms. Main's trial-45 redundant save remains a retained baseline failure; this branch does not modify frozen main.

## Retained development failures

| Test phase | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| diagnostic | 9 | 1 | 0 |
| diagnostic2 | 1 | 1 | 0 |
| focused | 110 | 3 | 0 |
| focused2 | 111 | 2 | 0 |
| focused3 | 112 | 1 | 0 |
| focused4 | 112 | 1 | 0 |
| focused5 | 112 | 4 | 0 |
| focused6 | 115 | 1 | 0 |
| focused7 | 111 | 0 | 0 |
| full | 285 | 0 | 1 |
| red-long | 4 | 3 | 0 |
| red | 6 | 1 | 0 |

All phase stdout and JUnit bytes are retained in `test-phase-artifacts.zip`, verified against `archived-test-artifacts.json`; the final passing focused/full outputs are also directly readable here. All exit and wake records are retained. Raw pytest failure bodies include trailing whitespace and are archived unchanged rather than edited or exempted from whitespace checks. Earlier trials, including trial 45's malformed hash, unanswered tool question and main protocol violation, remain untouched. The initial short fixture accidentally included definitions even with poor selection; the longer fixture exposed that failure. New-test whitespace and source-layout mistakes are documented in `development-notes.json`; the final fact assertion checks the entire evidence packet rather than requiring a particular excerpt order. No requested fact or old assertion was removed.

A broader nonliteral semantic admission, raw-logit ordering and candidate-pool backfill were explored and rejected: the local ranker strongly preferred metadata and eventually admitted an unrelated encryption question. Those runtime changes are absent from the final diff. Their failures and a rejected source/test snapshot are retained; individual uncommitted intermediate variants were not all separately archived. The rejected approach's five score-boundary cases are in that snapshot, not counted among the eight final regressions. The final runtime restores original `store.py` except the section-context opt-in.

## Limits and required gates

**No local/native benchmark, model profile or analyzer replay ran.** More context and instructions can increase tokens; performance is unmeasured, with no savings claim. All eight original contexts remain strictly required under active main ruleset 25440133 with no bypass. Only tests/lint is green for this source; all seven benchmark-dependent contexts are pending and blocking. Historical test/local successes are not carried to changed runtime. No current eight-gate score is asserted.

Current main fetched as `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. Canonical source/harness/model hashes, required-rule snapshot and verification are retained. No separate test-process import proof was collected. All temporary wake requests released; persistent power configuration did not change. `sha256.json` covers every other checkpoint file using published UTF-8/LF bytes. PR #3 stays draft, unmerged and without auto-merge. Pause after publication; no further work or run is scheduled.
