# Benchmark realism and independence

This change makes benchmark outcomes auditable. Its acceptance criterion is that
the evaluator correctly measures and reports outcomes, including genuine product
failures. Improving Colony's runtime or making every performance comparison pass
is outside this change.

The original evaluator allowed a baseline/feature dependency mixture, relied too
heavily on agreement between implementations, and accepted point estimates without
adequate quality and uncertainty checks. The independent worker loads every Colony
module from one selected source revision. A source-based oracle checks facts,
scope, fingerprints and excerpt locations. Both arms' compliance and every feature
answer's core facts and citations are evaluated. Required performance claims retain
their existing confidence requirements.

## What remains unchanged

Application files under `src/colony/` and dependency configuration match main
`6fa6826aa4c835001c0d2bc8f3bec838499e5b5d` byte-for-byte. The 24-question live task
set, outer prompts, research adapter, GPT-6 Luna low choice, task order, budgets,
oracles and gate definitions retain their frozen contract. This extraction retains
the seven final evaluator scripts from `164b126c97ec06dcb494618dd959057250b4e513`.
It excludes subsequent product-runtime changes and their product-specific tests.

## Evaluator validation

Provider-free controls reject constant misses/hits, shared wrong facts, forged
fingerprints and source lines, scope escapes, missing outcomes, incorrect token
accounting, changed questions, rejected saves, over-budget calls and unsupported
improvement claims. Conflicting import paths exercise implementation isolation.
Raw retained observations and frozen workload bytes are checked independently of
reported summaries. The genuine trial-22 input fixture is preserved unchanged.

An A/A comparison must not establish an improvement even if noisy timing happens
to favor one arm. A nonzero comparison exit for no supported win is the expected
control result, not a failed evaluator or a reason to tune application code.

Validation logs for this candidate are retained separately and must distinguish
test/control outcomes from product-comparison gate results. No historical trial
is relabeled as a measurement of this candidate. Coordinator quality labels are
nonblind wherever that is how the underlying evidence was produced.

## Measurement boundaries

The local benchmark times warm asks only; initialization, pushes, model provisioning,
cold starts and concurrency are excluded. It cannot establish full agent-workflow
savings. The native runner records per-task process elapsed time and native
`turn.completed` usage. That usage does not prove full account billing or include
every hidden CLI startup completion. It supplies no exact per-tool token or timing
allocation. Gross and cached-subtracted totals are separate token measures, not
measured dollar charges. Coarse account percentages do not establish exact spend.

Repeated public repository questions are development regression evidence, not
held-out production evaluation. Frozen-source main/feature comparisons test two
Colony implementations. They do not establish Colony-versus-no-Colony savings;
that requires a properly matched study. Legacy pre-audit savings claims are not
current strict evidence. See [the protocol](benchmark.md) for the fixed runners.

## Required checks and archival separation

All eight existing required checks and their semantics remain unchanged. Evaluator
self-tests passing is distinct from all product gates passing. A benchmark-only
change can remain blocked by performance-win requirements; resolving that merge
policy is a separate decision. No status is fabricated, waived or inherited onto
a new revision. Merge policy is not a reason to add runtime optimization to an
evaluator-validity change.

The complete runtime history, all old trials, failures, labels, investigation
records and diagnostic packages remain separately preserved. Their exact source
identities and archive locations are indexed in [the evidence record](benchmark-evidence.md).
