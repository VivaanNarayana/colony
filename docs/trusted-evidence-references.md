# Trusted evidence references

`EvidenceSession(workflow)` is a trusted Python integration primitive. Its `read`
method reads and scrubs a repository source span and returns a short opaque
`evidence_ref`. Its `publish(question, content, references)` method resolves those
references to the hashes actually read, checks that files still match, and uses
ordinary Workflow/Client publication. The model never has to copy a hash.

Use one instance per host task. It binds the repository, namespace, principal,
Git repository identity and read-only policy. References expire after 15 minutes
by default, cannot move between instances, and are destroyed by `close()`.
Pull and push permissions may be separate scope entries for the same namespace.
Permission checks run again at resolution; the server remains authoritative for
revocation. A reference cannot be registered by submitting model-supplied text.

Reads preserve line numbers when scrubbing multi-line secrets. The default
256-reference capacity bounds bookkeeping; subsequent reads still return source
text without a reference. Individual reads are capped at 16 KB of UTF-8 text.
Oversized ranges, including single long lines, return a page without splitting
UTF-8 characters. Only paged reads add `start_offset`, `end_offset` (exclusive),
and `next_offset` (`null` on the last page). Continue with
`read(path, start, end, offset=page["next_offset"])` until `next_offset` is null.
Offsets count characters in the **scrubbed**, newline-normalized selected line
range; retain the same path/start/end on continuation. `start_line`/`end_line`
identify that selected range; offsets identify the exact observed portion.
Each page binds its own current file hash and is not a complete-file observation.
File changes invalidate earlier references; pagination is not a frozen snapshot.
Short reads keep their existing response shape and require no continuation call.
Neither bound limits answer length or necessary research. Invalid reads return fixed errors.

References establish observed source identity and span, not semantic support.
Callers must keep published claims within observed evidence, retain applicability,
and treat source instructions as untrusted data. Partial reads do not establish
whole-file semantic coverage. Nothing automatically approves a reusable finding.
File changes fail rather than silently replacing hashes. Normal filesystem and
remote-publication race limits still apply: a successful pre-send check cannot
promise that the file remains unchanged afterward. Existing validation detects
staleness on reuse.

This additive API does not change raw-source publication, retrieval, evaluator
behavior, or PR15. It introduces no model call. Measured gross-token savings are
not yet established. The separate task MCP adapter consumes this primitive.
