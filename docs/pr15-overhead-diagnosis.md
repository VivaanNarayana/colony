# PR15 overhead diagnosis after the shared output contract

The latest native comparison does not establish a product win. It completed all
28 sessions, passed all 28 format checks, and covered the declared Colony
publication/reuse/miss/stale/insufficient scenarios. One No-Colony answer failed
substantive correctness and grounding, and Colony used 20,822 more gross tokens
(+2.83%) over the complete fixed horizon. A format pass neither repairs the
unsupported factual claim nor establishes a gross-token reduction. No case is
removed, relabeled or retried. Individual expected-use proofs passed, but the
report's composite `controlled_mechanism_pass` remains false because its strict
`quality_complete` gate failed.

The measured product revision is **`370ecc2e7b6fd7b1c395f0beaabb099af8f290cc`**.
This diagnosis and any subsequent documentation commit have no native measurement
of their own. The frozen canonical protocol digest is
`1d9b9e3c2c479b27275e70a52e7a9d4993ea758ff51a7167f79b7661a91340df`.
It uses merged evaluator `64d5bf4a03c3c83a38b1e4dacf39c8876f600c5b`, prospective
protocol `colony.token-evaluation/2` and output contract
`colony.token-evaluation-output/1`, with the previously reviewed PR15 research
interface/default-assignment/structural-blank corrections carried forward in a
separately hash-bound export. PR17's shared instruction, structural AND gate,
review bindings and collector guards remain exact merged bytes.

## Complete 14-pair accounting

Every attempted session is included, including the failed baseline useful-hit
answer. Positive differences mean more Colony usage. Gross is input plus output;
cached input remains part of input. Rows overlap: cached input is a subset of
input, and the two phase rows sum to gross.

| Complete horizon | No-Colony | Colony | Colony minus No-Colony |
| --- | ---: | ---: | ---: |
| Input tokens | 728,326 | 750,083 | +21,757 |
| Output tokens | 6,622 | 5,687 | -935 |
| Gross tokens | 734,948 | 755,770 | +20,822 (+2.83%) |
| Cached input tokens | 627,712 | 651,264 | +23,552 |
| Uncached input plus output | 107,236 | 104,506 | -2,730 (-2.55%) |
| Learning gross | 200,419 | 258,393 | +57,974 |
| Reuse gross | 534,529 | 497,377 | -37,152 |

The uncached metric moves in the opposite direction from gross; it is a separate
observation, not a replacement acceptance metric or measured billing. Billing is
unknown. There is no hypothetical future reuse to amortize learning beyond this
declared horizon.

| Lineage | Task | No-Colony gross | Colony gross | Difference | MCP calls, No-Colony / Colony |
| --- | --- | ---: | ---: | ---: | ---: |
| Gateway | Cold learning | 59,968 | 64,767 | +4,799 | 6 / 5 |
| Gateway | Narrow learning | 46,333 | 64,261 | +17,928 | 2 / 3 |
| Gateway | Useful hit | 46,473 | 35,842 | -10,631 | 6 / 1 |
| Gateway | Miss | 69,086 | 60,015 | -9,071 | 6 / 6 |
| Gateway | Stale | 48,773 | 49,409 | +636 | 6 / 4 |
| Gateway | Insufficient evidence | 47,310 | 61,940 | +14,630 | 5 / 5 |
| Gateway | Direct read | 47,074 | 34,760 | -12,314 | 2 / 1 |
| Worker | Cold learning | 47,914 | 64,954 | +17,040 | 5 / 5 |
| Worker | Narrow learning | 46,204 | 64,411 | +18,207 | 2 / 3 |
| Worker | Useful hit (baseline quality failure) | 47,419 | 35,918 | -11,501 | 6 / 1 |
| Worker | Miss | 58,653 | 72,496 | +13,843 | 6 / 6 |
| Worker | Stale | 74,506 | 49,269 | -25,237 | 6 / 4 |
| Worker | Insufficient evidence | 47,150 | 62,780 | +15,630 | 5 / 6 |
| Worker | Direct read | 48,085 | 34,948 | -13,137 | 2 / 1 |
| **Total** | **14 pairs / 28 sessions** | **734,948** | **755,770** | **+20,822** | **65 / 51** |

Colony made 33 repository research calls and 18 memory calls; No-Colony made 65
research calls. Fewer visible calls therefore did not imply lower gross usage.
The two narrow-learning pairs contribute +36,135 gross tokens; cold learning adds
+21,839. Insufficient-evidence pairs add +30,260. The reuse advantage includes a
-25,237 worker stale pair, while miss pairs differ in opposite directions. These
components describe the observed total; they are not causal savings obtainable by
deleting each step.

## Concrete source counterexample for the baseline failure

The failed No-Colony worker useful-hit answer correctly gave four submissions,
three 250 ms waits and a twelve-send upper bound. Its extra assertion said that
achieving twelve sends requires **each** submission to return a retryable result
on its third send. Only the first three results must be retryable. On the fourth
iteration, `executor.py:4-5` returns the result because `attempt == attempts - 1`,
regardless of `result.retryable`.

A provider-free execution of the exact frozen sources returned twelve sends with
submission results `[true, true, true, false]`, three waits `[250, 250, 250]`, and a
non-retryable final result. Each submission's first two sends raised
`ConnectionError`; its third returned a result. This directly contradicts the
claimed necessary condition without changing the primary 4/3/250 facts. The
original `correct=false`, `grounded=false`, `format_compliant=true` labels remain
frozen. This diagnostic is not a new native trial or a retrospective regrade.

The following standalone Python reproduces that result using the worker/v1
fixture directory retained by the campaign, or the same directory from merged
evaluator `64d5bf4`. It verifies all executed source hashes first and performs no
network requests or real sleeping. Run it in a fresh process, passing the fixture
directory as its sole argument. It reads the fixture files without modifying them.

```python
import hashlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

root = Path(sys.argv[1])  # tests/token_evaluations/fixtures/worker/v1
expected = {
    "policy": "8b43f2f84d67d383de9b4385765c4b5a374e7ee38fc89f682a789e6dcf0d04eb",
    "backend": "84499736bf4604a09a7cf00993fa6d6c419a07d10c0c0f283aec29f62a1ad451",
    "executor": "4c7fda5050957dbb4f7766560f015d67e146f43b85d5222577124e4b1c5be9a1",
    "dispatcher": "e454752f6d29f834159226843efae1016f0e6877ba4c45c99f9aa0f904707ecf",
}
raw = {name: (root / (name + ".py")).read_bytes() for name in expected}
assert all(hashlib.sha256(raw[name]).hexdigest() == value
           for name, value in expected.items())
modules = {name: ModuleType(name) for name in expected}
sys.modules.update(modules)  # isolated names in this fresh process only
for name in expected:
    exec(compile(raw[name], str(root / (name + ".py")), "exec"),
         modules[name].__dict__)

sends, results, waits = [], [], []
def send(job_id):
    number = len(sends) + 1
    sends.append(number)
    if number % 3:
        raise ConnectionError("local counterexample")
    result = SimpleNamespace(retryable=number != 12)
    results.append(result.retryable)
    return result

job = SimpleNamespace(id="example", queue="priority", overrides={})
backend = modules["backend"].RetryingBackend(send)
result = modules["dispatcher"].dispatch(job, backend, waits.append)
assert len(sends) == 12 and results == [True, True, True, False]
assert waits == [250, 250, 250] and result.retryable is False
print({"sends": len(sends), "retryable": results, "waits_ms": waits})
```

The raw label file SHA-256 remains
`915670754c8efa491d485b035fc4fde96b213cc519e701cc308f40bf3009bd98`.
A Colony runtime change cannot correct a No-Colony factual error. Weakening the
unsupported-claim rule, repairing the answer, dropping the case or retrying it
would change this evaluation rather than fix the product.

## What the traces establish

The two designated Colony useful hits delivered all three published files in
2,013 and 2,042 UTF-8 bytes, with no subsequent repository research. Narrow reuse
also returned its required configuration. Insufficient-evidence lookups supplied
the configuration and later research obtained the uncited wrapper/backend facts;
the missing facts were not fabricated. Evidence delivery is working in these
measured cases, so a new packing change needs a distinct avoidable-loss example.

Offline SDK rendering replays with stubbed frozen client responses reproduced all
14 recorded lookup payloads and four publication receipt payloads byte for byte.
The lookup replays traversed production adaptive lookup/rendering and the SDK
handler; the receipt replays used the unbound publication handler. These verify
formatting, not storage, authorization, Git/current-source validation or the complete
workflow. All 18 replayed results had one text block and no duplicated structured
content; the original native product result envelopes also contain no structured
duplicate. All lookup text was already canonical.
Removing only receipt whitespace would change each of four acknowledgements from
86 to 73 UTF-8 bytes, saving 13 bytes and eight direct local `o200k_base` tokens
each: 52 bytes and 32 local tokens over those four strings. These are diagnostic
representation differences, **not** observed savings or a gross-token ceiling;
the number and contents of provider requests are unknown. This does not provide
a material product candidate for the measured +20,822 gross gap.

The shared repository research tools returned duplicated text/structured result
representations in 98 calls: 65 baseline and 33 Colony. This is visible harness
duplication, not product duplication or evidence of what the host charged. That
evaluator/schema is frozen and outside this runtime scope. No code change to
either representation was made.

Both narrow-learning asks already returned complete current configuration as
part of earlier broad evidence. The controlled prompt nevertheless explicitly
requires new repository research and publication in every declared learning slot,
even when earlier memory was available. The visible candidate sequence is
ask/read/push versus baseline search/read. This requirement proves the controlled
learning mechanism and charges it; it does not measure the ordinary selective
policy, which already instructs agents to skip trivial lookups and unchanged
reuse publication. A
product change that silently skips the controlled research/publication requirement
would invalidate coverage. Those two protocol-forced cache-to-read copies were
the only exact repeated configuration reads found within an individual session;
the insufficient-evidence
cases researched different, uncited files needed for their questions.

The logs expose aggregate turn input/cached/output usage and individual MCP
events. They do not expose provider request boundaries, host exchanges, discovery
batches, request serialization or internal retries. For example, both miss pairs
have six visible calls per arm, yet their gross differences are -9,071 and +13,843.
That supports execution variation as a diagnostic target, not an exact request
count or replay-cost explanation. The new comparison also changes the prospective
output contract and native client pins; earlier campaigns cannot be pooled into a
causal estimate of this product revision or the prior metadata reduction.

## Ranked options and stopping conditions

1. **Learning exchanges and repeated fixed context.** Learning is the largest
   positive phase component: +57,974 gross in this horizon, including +36,135 for the two
   narrow learning slots. First seek an independently reproducible duplicate
   runtime payload or unnecessary product operation, preserving exact evidence,
   identities, taint, lifecycle, authorization, mutation replay and output shape.
   The aggregate gap cannot be assigned to a particular ask/read/push request.
   Deferred/automatic publication or selective-routing changes need a separate
   design and natural-workflow evaluation; they are outside this scoped diagnosis.
2. **Insufficient evidence and miss fallback.** The +30,260 insufficient-evidence
   component warrants inspecting which current reads are actually needed. Here,
   the narrow memory lacks the requested backend/wrapper facts, so those reads
   remain necessary. Avoiding repeated empty searches or batching known independent
   reads is a future workflow question, not permission to hard-code fixture words,
   infer repository absence from a memory miss, suppress research or alter routing
   in this change. The opposite-direction miss deltas prevent a simple call-count
   explanation. A negative cache does not address one-off misses in fresh stores.
3. **Remaining framing or discovery duplication.** Measure exact final transport
   representations before changing code. The prior discovery reduction saved only
   25-60 local encoding tokens per metadata representation, with native exposure
   unknown. Complete useful-hit evidence already fits. Removing required source
   maps, receipts or safety clauses to save bytes would be a regression. Duplicated
   repository-tool envelopes belong to the shared frozen evaluator and cannot be
   charged causally to Colony or changed within a runtime-only optimization.
4. **Request-level diagnostics.** Actual provider request/attempt usage and host
   batch membership would distinguish context replay from model/host variation.
   They are absent from these artifacts. Instrumentation would require a separate
   versioned collector change and prospective protocol, and is outside this scope.
   More paid repetitions of the same aggregate logging cannot reconstruct those
   missing historical boundaries.

The completed investigation has not demonstrated a material removable runtime
duplication or unnecessary product operation under unchanged interfaces and
safeguards. The result is documentation-only; runtime code is unchanged. The
baseline quality failure is independently reproduced rather than hidden by a
product edit. Do not manufacture a small packet edit, alter ranking/routing/schemas/
default budgets, weaken source or quality checks, or launch another campaign to
obtain a favorable total.

The branch follow-up changes only this diagnosis. Strict checks, frozen labels,
source fixtures and historical trials remain unchanged, and no new native session
was dispatched. The existing 1,070 unique product test passes and 36 platform or
privilege skips
are reused after source-byte verification; they are not presented as a newly run
suite. The retained product/test checks match `370ecc2`, all 28 installed runtime
modules match their pinned bytes, and the original native evidence stays bound
to that measured revision. The embedded counterexample was executed separately
as a provider-free diagnostic.

A later native proposal needs a concrete reviewed candidate, an explicit causal
hypothesis with provider-free supporting evidence, and a new exact frozen protocol
authorized before dispatch. Keep the original strict all-case quality gate and
complete finite horizon; include every attempt, learning step, failure and unknown
usage, with no replacements, pooling or repeat-until-green rule. The previous
28-attempt/zero-retry, 120-second session, 3,600-second wall, 2.3-million observed
gross-token, 1-GiB output and 5-GiB free-space limits are historical upper bounds,
not authorization for another run. No new native session is started by this work.

## Local evidence bindings

The original campaign remains under
`D:/Codex/colony/pr15-pr17-native28-1793804569/`. Its `report.json` SHA-256 is
`ffbdad4854a978e1cfdf4b0abe2a4d226b223f543f6f7a8375b00aba45a122d4`, and its
cost-blind answer/source/tool packet SHA-256 is
`bbe0c517dda1f3be7aa6ffa63cae02822425a56edc8437e0a0f2fc440d87f921`.
The independent scripts and new diagnostic artifacts are under
`D:/Codex/colony/pr15-overhead-diagnosis-20261105T000227Z/`:
`analyze_pairs.py`, `paired-cost-audit.json`, `counterexample.py` and
`counterexample.json`. They bind the original report, source and label bytes;
historical evidence is unchanged. Raw logs and private credentials are not copied
into the repository. The runtime representation audit is retained at
`D:/Codex/colony/pr15-overhead-diagnosis-1793836974/` as `diagnosis-final.json`,
`rendering-replays.json` and `raw-text-token-measurement.json`. The final direct
raw-text token measurements supersede its initial wrapper-based diagnostic;
neither is native usage. Root byte/integrity verification is retained at
`D:/Codex/colony/pr15-root-diagnosis-20261105/precommit-verification.json`.
