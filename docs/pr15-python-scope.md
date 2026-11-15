# Python scope in adaptive excerpts

Adaptive `.py` and `.pyi` excerpts retain complete top-level statements enclosing
the selected paragraph. This keeps function/class headers, branch conditions,
decorators, exception handlers and multiline expressions attached across blank
lines. Unrelated top-level statements outside the selected paragraph stay out.
Two selections in the same enclosing statement share one excerpt without making
room for a third ranked passage. The original two-passage limit remains in force.

The parser inspects already scrubbed source and never executes it. Original source
text, coordinates and file hashes remain the evidence; the AST is only a boundary
selector. Parsing happens once per selected file per lookup, with no persistent
source cache. Existing shell/shebang and Markdown handling stay unchanged.

Unsupported or malformed Python uses a conservative whole-source unit. The existing
response budget still withholds an oversized required unit rather than clipping
away its scope. A class or large function can therefore require independent source
research when it cannot fit; this change does not promise complete dependency
coverage or remove the caller's obligation to inspect gaps.

No prompt, tool schema, answer limit, publication rule, retrieval ranking or
evaluator changes are included. No automatic model/tool calls are added. Local
regressions establish source coverage and bounded payload behavior, not gross-token
savings. Historical evaluation results remain unchanged.
