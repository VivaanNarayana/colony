# Reranker metadata instrumentation

This draft implements the bounded metadata engine and application hooks from the
telemetry proposal. **Collection is disabled.** There is no CLI option,
environment variable, HTTP endpoint, startup registration or production activation
API. Importing the modules and using Colony does not construct a recorder, open a
telemetry file, start a writer/watchdog, inspect tensors, sample requests or install
backend wrappers. Internal binding and backend preparation exist for synthetic
provider-free tests only.

The work starts from main `b3e4bc746d60092e2242c18fa54985c10eae8316`, independently
of PR #4. Reranking still uses `model.rerank(question, passages)` and FastEmbed's
default batch size. Models, weights, two-thread CPU settings, candidate selection,
thresholds, validation, security behavior and response data are unchanged. PR #4's
failed required local gate remains failed; none of the eight acceptance criteria
is changed or satisfied by this instrumentation.

## Observation boundary and accounting

An ask is a top-level `Store.ask()` entry. HTTP authentication, malformed requests
and forbidden scopes rejected before that boundary are excluded. Immediate
no-namespace returns and application exceptions are included. Context variables
keep asks, stages and rank calls request-local; tokens are reset in `finally`.
Nested asks stop observation rather than silently enrolling the same request twice.

Four call-site enums cover the actual imported rank aliases: `answer`, `evidence`,
`excerpt_claims`, and `excerpt_plain`. The common rank decorator observes the empty
fast path, which still returns without constructing models. Missing attribution
stops the observer and leaves the application operation intact.

All enrolled asks contribute population counters, even when not selected for
detail. Those counters distinguish rank entries from nonempty rank entries,
zero/one/multiple calls per finished ask, multi-passage work, normal/error outcomes,
and unfinished asks. A random 10% Bernoulli decision is made once at ask entry,
independent of its content or eventual outcome. The entire selected ask is the
sample cluster. There is no width-based selection, singleton filtering,
replacement sample or adaptive rate.

Safety limits can censor selected long or busy asks. Counters distinguish selected,
admitted, complete, censored and unobserved asks. A censored ask that has not yet
shown multi-passage work is unknown, not a confirmed negative. The triggering
entry may be counted before a safety stop, without being admitted for detail.
Retained details after a safety stop are not an unbiased 10% sample.

## Metadata and input boundaries

The encoder accepts a closed schema: fixed enums, unsigned 64-bit integers,
fixed-size histograms, fresh 32-character run-local join IDs, and bounded software
revision/digest/version identifiers. No arbitrary object or exception conversion
is used. The engine never retains question/passage/answer text, token IDs or token
strings, embeddings, logits, content hashes, paths, namespaces, trail/member/user
IDs, credentials, exception messages or tracebacks. Code/model artifact digests
are provenance fields, not hashes of request or evidence content.

Passage-count bins are `0, 1, 2, 3–4, 5–8, 9–12, 13–16, 17–32, 33–48, 49–64,
65–96, 97+`. Character bins end at `0, 64, 256, 1024, 2400, 4000`, followed by
overflow. Pair-token bins end at `0, 16, 32, 64, 128, 256, 512`, followed by overflow.
Histograms have fixed lengths; neither record size nor retained state grows with
input text or token count.

The synthetic backend adapter dispatches through the original preprocessing and
inference methods exactly once. It observes only an already-produced integer/bool
attention mask. Type, dimensionality, row bound (96), and sequence bound (512) are
checked before any mask reduction. The adapter retains only scalar reductions,
never tensors or encodings. It is installed once before test requests are bound,
not installed/removed around concurrent rank calls. Stopped observers forward the
original backend unchanged.

An attention mask describes **post-truncation combined query–passage nonpadding
tokens**, including special tokens. It does not reveal separate passage length,
pretruncation length or whether truncation occurred. Slots are summed over actual
batch shapes, including a 64-plus-1 split. Attempts and completions are separate;
missing observation is `unavailable` with null token fields, never measured zero.
Partial observation is labelled `partial`; an empty rank is `not_applicable`.

Only the caller's existing operation runs. There is no extra tokenization,
embedding, model pass, retry, download or provider call. Real FastEmbed adapter
compatibility remains unverified: the allowed checks use fake backends only.

## Bounds and failure behavior

Fixed ceilings can be lowered by tests, never raised by configuration:

| Bound | Ceiling |
|---|---:|
| Window | 60 minutes |
| Enrolled asks / rank entries | 1,000 / 4,096 |
| Admitted detail samples / rank records | 128 / 1,024 |
| Rank entries per selected ask | 8 |
| Active asks / selected active asks | 64 / 16 |
| Queue | 64 serialized records, 4 KiB each |
| Local output | 5 MiB, including a reserved 16 KiB footer budget |
| Accounted callback/writer CPU tripwire | 2 seconds |
| Cumulative selected rank / ask foreground work | 0.25 ms / 1 ms |
| Flush grace | 1 second |

Resource checks are operational tripwires, not hard real-time or measured overhead
guarantees. A stop-next-event check cannot undo an overrun. The proposal's 2 MiB
collector-memory design target and 10/20 microsecond unsampled-work targets have
not been measured or established. Queue payload size alone does not bound Python
object memory. No benchmark or overhead measurement is part of this draft.

The optional explicitly constructed local sink uses exclusive creation in a
caller-supplied existing private directory, no overwrite, no spill and no network
transport. POSIX mode 0600 is requested; Windows privacy depends on the directory's
existing access controls. It is never constructed by the application. Writer work
is accounted on its own thread, rather than using process CPU that includes model
inference.

A queue, byte, identity/stage/schema, shape, contention, observer, writer or budget
failure stops observation. Observer work cannot change results, retry the model or
replace/swallow the original application exception. Serving callbacks never wait
for sink flushing. A separate optional watchdog/explicit test teardown handles
closure. In-flight asks/ranks are censored at closure; later returns cannot change
that snapshot into completions.

The writer reserves closure space before accepting normal records. A complete
footer records queued/written/lost records and censoring. A missing footer,
unflushed rows or writer failure makes completeness unknown or partial, not zero
loss. A blocked filesystem operation may outlive the join deadline on its daemon
writer; that cannot establish durable closure. The engine does not automatically
restart, resume, replay or relax a cap.

Observer duration fields are accounting snapshots before their record is encoded;
global CPU accounting additionally includes subsequent observer/serialization work
and writer work. Durations describe instrumented execution, not unperturbed model
latency. Do not subtract these clocks to infer a causal batch-policy comparison.

The CPU counter currently covers timed collector callback bodies and writer CPU
through its latest checkpoint. It excludes constructor/header work, the watchdog,
dispatcher/context bookkeeping, some accounting/tripwire work and terminal
closure. **It is not a whole-collector CPU cap.** The proposal's whole-collector
two-second target, like the memory and latency targets, remains an activation
prerequisite that this provider-free draft does not establish.

Stop requests deny new callbacks. An observer update already holding the lock may
finish before the close-time snapshot; closure is linearized under that lock.
Callbacks recheck stop/closed state after acquiring it and cannot mutate an
already-closed snapshot. Footer counters explicitly classify rejected rank entries
and selected ask/rank records lost before enqueueing. `complete_samples` and
`events` count enqueued summaries, not durable records; writer losses must also be
examined.

A footer is necessary but not sufficient for complete closure: the writer must be
terminated and `Recorder.closure_ok` must be true. Late writes suppress or retract
their footer where possible. A false/unknown closure acknowledgment makes the run
incomplete even if bytes are temporarily visible. A future activation controller
must retain this acknowledgment; this draft has no such production controller.

## Before any future activation

A separate reviewed activation change and explicit collection authorization must
identify the owner-approved cohort/process, immutable application/observer/model
identities, already-initialized models, private destination, fixed window and caps.
The current identity encoder validates shape, not an attestation of a deployment.
It must not be treated as proof that the labelled software or weights are loaded.
Nothing in this draft starts or warms models to establish identity.

That later scope must verify runtime adapter compatibility, request/executor
context propagation, memory and overhead targets, private directory permissions,
closure under failures, and retention handling. The proposed seven-day lifetime
applies to future raw records and temporary copies, not historical benchmark
evidence. This draft schedules no deletion or collection job.

There is no export or publisher. Future aggregate release requires explicit
approval, at least ten distinct sampled asks per cell, and complementary
suppression where totals could reveal small cells. This is metadata, not a claim
of anonymity. It can characterize one observed process/window; it cannot establish
production representativeness, counterfactual speed, quality, or any benchmark gate.

## Provider-free checks

The tests inject a memory sink, deterministic clocks/randomness, synthetic asks,
fake rerankers and small NumPy masks. Model constructors are forbidden. They cover
default inactivity, whole-ask sampling, empty input, alias/stage attribution,
forwarding/order/float conversion, 64-plus-1 batch accounting, original exception
identity, schema rejection, safety censoring, concurrency and explicit synthetic
sink closure. They collect no application traffic and perform no real model or
tokenizer work, live trial, benchmark or performance acceptance validation.
