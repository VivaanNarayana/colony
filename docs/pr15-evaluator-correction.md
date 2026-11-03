# PR15 evaluator correction (offline preparation only)

PR15 retains its source-identity compatibility fix. This developer-only overlay
addresses misleading research-tool interfaces and overly broad evidence coverage.
A narrow runtime correction preserves explicit whole-file requests for small
published source bundles instead of losing files to the two-passage cap. Default
budgets, non-adaptive behavior, dependencies and permissions remain unchanged.
This work does not dispatch model sessions or establish token savings.

The evaluator merged in PR16 is not an ancestor of PR15. The small reviewed patch
in `scripts/pr15_evaluation_overlay/correction.patch` is applied to exact Git blobs
from `1ee4cd787b9cf65d8df82cbcf9142ad0208208e7`. The manifest binds every original
file, the patch and each patched file by SHA-256. Preparation rejects unexpected
paths, links, changed inventories and existing output directories. It fetches
nothing, installs nothing and changes no branch. If that Git object is unavailable,
preparation fails; obtain the exact object from the repository before retrying.
Failed/partial exports are retained and a retry requires a new destination.

On Windows, first use the existing D-storage environment setup and provisioned
offline ONNX snapshots. Keep all temporary directories, caches, test exports,
receipts and logs on D:. The optional tokenizer uses its verified local data file;
no download fallback is added. Preparation pins LF bytes independently of the
user's Git CRLF configuration.

```powershell
python scripts/pr15_evaluation.py --output D:/evaluations/pr15-correction-001
python -m pytest tests/pr15_evaluation -q
```

The encoding-token journeys use the explicit `COLONY_TEST_CONTEXT_TOKENIZER`
fixture and provision `COLONY_CONTEXT_TOKENIZER` within each test. Without that
fixture or the optional `tiktoken` dependency, only those two journeys skip;
byte-budget journeys and broken-publication/always-miss controls still run.
No runtime tokenizer environment setting is required in the invoking shell.

Run the original evaluator controls against the patched disposable export too:

```powershell
Set-Location D:/evaluations/pr15-correction-001
# Use the development environment with the current PR15 product installed.
python -m pytest tests/token_evaluations -q
```

The runner's only action is export/patch/hash verification. It has no native-run,
authorization or provider option. `overlay-receipt.json` identifies the exact
prepared sources and explicitly records zero native sessions.

## Corrections and controls

* Actual MCP descriptions explain file listing, direct reads, the 120-line maximum,
  and nonempty case-sensitive literal content search. Regex, filename and semantic
  search are not implied. Both arms receive the same interfaces. Invalid attempts
  still consume the six-call research allowance; bounds and strict integer types
  are unchanged. Empty search strings are rejected by the exposed schema too.
* The narrow default-setting oracle requires its defining assignment, rather than
  unrelated override-function lines. Typed answer facts, task questions, snapshots,
  ordering, source identities and the fixed reuse horizon are unchanged.
* Coverage may omit structural blank lines only in syntactically valid Python
  whose full bytes match the frozen source hash. It never rewrites source/excerpt
  text. String-literal whitespace, prose, invalid Python and unsupported interpolated
  string token forms retain strict coverage. Missing required code, changed hashes,
  altered indentation/text, wrong memory identity, stale evidence and missing
  research still fail. Blank-only oracles cannot establish reuse. Without source
  context the coverage helper retains the original strict behavior.
* Positive journeys use actual MCP, Client, authenticated HTTP, Store, SQLite and
  offline ONNX models; only HTTP transport is adapted to local ASGI. They verify
  empty learning, persisted publications, fresh read-only reuse without repository
  calls, misses, source changes, current fallback and partial-evidence research.
  Deliberate no-op publication and always-miss runtime controls must fail their
  specific contracts. No positive retrieval/publication result is fabricated.

## Budgets and interpretation

The full offline journey is tested separately at 4,096 UTF-8 bytes and 2,048
`o200k_base` tokens. A 2,048-byte multi-file journey is a deliberately tight
diagnostic: an honestly reported `budget_limited` result is not a verified full
journey or a memory-reuse success. Tests do not increase a requested limit to fit.
Every final hit/miss receipt must count the complete response, including source
identity maps. Existing product boundary/transport tests retain safe fallback.
The new real integration failed before the packing correction: a 2,048-token
response used only 525/659 tokens but omitted required published files. Explicit
whole-file requests now preserve all files when there are at most four and their
combined text fits the existing 2,800-character excerpt allowance. The complete
bundle is required and is still subject to final protocol counting; it is never
silently clipped to make a hit. Larger bundles and focused requests retain the
ranked selection path.

Byte counts and offline encoding-token counts are not native model usage or
billing. No default is changed and the two units are never pooled. Runtime packing
changes require a reproducible avoidable-loss case; an undersized budget alone is
not such a case and does not justify removing required evidence.

Historical native protocols, raw logs, labels, measurements and failed statuses
remain immutable. Corrected coverage must not silently relabel old campaigns.
Any future native comparison needs a fresh frozen protocol identifying this
overlay, its patched collector/catalog hashes, exact product/client revisions,
explicit budget units, independent quality review and separately authorized limits.
The template records the overlay version. Live preflight and execution require
exact active collector pins, including when called through their direct entry
points. Missing, empty, null or mismatched collector bindings fail before local
service provisioning, credential-file access or model dispatch; native reports
apply the same guard. Provider-free fixture collection remains available without
native collector pins and cannot certify savings. Regression controls intercept
every live boundary and verify both rejection and the valid protocol path.
All learning, failure and retry costs remain included. Passing these offline
controls cannot turn the native token evidence check green or authorize a merge.
