# Trusted evidence references

`EvidenceSession(workflow)` is a trusted Python integration primitive. Its `read`
method reads and scrubs a repository source span and returns a short opaque
`evidence_ref`. Its `publish(question, content, references)` method resolves those
references to the hashes actually read, checks that files still match, and uses
ordinary Workflow/Client publication. The model never has to copy a hash.

Use one instance per host task. It binds the repository, namespace, principal,
Git repository identity and read-only policy. References expire after 15 minutes
by default, cannot move between instances, and are destroyed by `close()`.
Permission checks run again at resolution; the server remains authoritative for
revocation. A reference cannot be registered by submitting model-supplied text.

Reads preserve line numbers when scrubbing multi-line secrets. The default
256-reference capacity bounds bookkeeping; subsequent reads still return source
text without a reference. Individual reads are capped at 16 KB of UTF-8 text;
request smaller line ranges for larger files. Neither bound limits answer length
or the number of necessary research operations. Invalid reads return fixed errors.

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
