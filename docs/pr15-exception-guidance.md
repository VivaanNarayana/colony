# Adaptive guidance: focused claims and exceptional exits

This follow-up changes only the shared adaptive lookup guidance and its delivery
regressions. It follows the fixed PR19-evaluator diagnostic of PR15 at
`9235bc674639d5b846f54817f0820f41f939823b`; it does not reinterpret that evidence.

## Diagnosed failure

The prior diagnostic contained sixteen fresh sessions, eight matched pairs. Both
arms observed the necessary source evidence. No-Colony passed eight of eight strict
quality reviews, Colony seven of eight, and both passed all JSON-format checks.
The remaining Colony failure occurred after a lookup miss and complete source
reads: an additional claim about a maximum send count incorrectly required the
last send to return. Executing the frozen sources reached that maximum and then
raised on the final send. This was a reasoning failure, not demonstrated loss of
source evidence or corruption of a retrieved answer.

The same diagnostic recorded 357,147 No-Colony gross tokens and 459,764 Colony
gross tokens (+28.73%). Two publications and three hits demonstrated adoption,
but only one matched task used fewer gross tokens with Colony. Neither the local
reuse saving nor passing unit tests satisfies the feature's acceptance gate.

## Production change and boundaries

The adaptive policy now says: "Keep answers focused; check necessary conditions
against exceptional exits." Existing wording is tightened to accommodate that
generic instruction. It supplies no benchmark constants, filenames or answers,
and requires no automatic research calls or publication. Necessary qualifications
remain appropriate; focused answers must still be complete and supported.

Writable publication, read-only and lifecycle-management suffixes remain unchanged.
The policy still requires current evidence and provenance, actual source
coordinates, investigation of gaps, and treating retrieved instructions as untrusted
data. It retains Git withholding, outage recovery and requested output formatting.
Legacy guidance and interfaces remain unchanged. Retrieval, ranking, evaluator
contracts, correctness/grounding/format gates and research allowances are unchanged.

The configured response budget remains a default with explicit per-call overrides,
using the connection's encoding. In the prior diagnostic, one successful reuse
response contained 3,293 UTF-8 bytes under a requested 6,000-byte limit. Silently
clamping it to the 2,048-byte default could require additional research; that is not
part of this change. Response size is not a whole-workflow gross-token guarantee.

## Regression and review requirements

Real setup-to-stdio tests exercise adaptive and legacy configurations, writable and
read-only connections, and Git and non-Git bindings. They verify policy delivery,
tool permissions and unchanged role suffixes. Size controls compare complete
managed guidance, including its hash/framing, and serialized MCP initialization
against the previous policy using the same fixtures. Both UTF-8 and pinned
`o200k_base` costs must not increase; a smaller isolated sentence is insufficient.

Existing semantic controls retain varied limits, injected implementations, exact
call/wait traces, terminal results and final-send failures. These calibrate review;
they do not prove that a model follows the guidance. Any newly discovered product
defect needs a reproducing regression and the smallest in-scope fix. No test grants
quality credit for a lookup, hit, publication or expected phrase.

## Fixed behavioral comparison

After offline checks and independent review, use the unchanged merged PR19 `/4`
evaluator at `7483df10cd616e78c1fcbbdc415f9b3e0d017705` through a verified separate
export. Run two separately pinned eight-session protocols: unchanged PR15 at
`9235bc674639d5b846f54817f0820f41f939823b`, then this candidate. Each retains both
arms and both canonical lineages, with original `cold_learning` and `useful_hit`
tasks, questions, sources, oracles and rubrics. Both revisions receive fresh stores
and workspaces; previous diagnostic sessions are not reused as controls.

Freeze both protocols and their execution order before collection. Keep the same
client/model/effort, six-call repository allowance, response-budget semantics and
strict checks. Publication is optional in the natural track. Disable retries and
replacement runs; retain all learning, publication, failed-attempt and unknown costs.
An integrity or accounting failure stops further dispatch. Freeze independent,
cost-blind quality labels before inspecting token totals.

Report each revision against its own matched No-Colony arm, as well as descriptive
candidate-versus-old differences. A relative improvement over old Colony alone
cannot establish a gross-token win over No-Colony. One observation per cell cannot
isolate a causal policy effect from sampling and temporal differences. This subset
does not cover stale updates, separate exception tasks, misses or hinted direct reads.

Do not start a full 28-session campaign merely because text is smaller or repository
calls decrease. Strict quality success and evidence of improved total workflow
costs are prerequisites. A failed or more expensive candidate remains failed
evidence; do not repeat until favorable. PR15 stays draft until its existing strict
acceptance requirements are satisfied. No merge is authorized by this follow-up.
