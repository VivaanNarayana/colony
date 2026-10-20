# Colony

Shared memory for one team's coding agents. Push a learned answer, ask before repeating
research, and check stored repository evidence on every pull. Self-hosted CLI + FastAPI;
no model API key, inference service, or telemetry required. Local retrieval models download
on the first server start (about 160 MB); provision them before disconnecting the host.

## Quick start

Python 3.11–3.13 with SQLite extension support is required. Run from this checkout:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
colony init --dir .colony/admin
colony issue --issuer .colony/admin/issuer.pem --name agent-a --push team --pull team --out .colony/agent-a.json
colony serve --repo . --data .colony/server --issuer .colony/admin/issuer.pub
```

In another terminal with the environment activated:

```sh
colony --home .colony/agent-a join .colony/agent-a.json --issuer .colony/admin/issuer.pub
colony --home .colony/agent-a push examples/trail.json --ns team --repo . --source pyproject.toml
colony --home .colony/agent-a ask "How do I install the development dependencies?" --ns team
colony --home .colony/agent-a run --capture outputs --ns team --question "How do I run the tests?" --source pyproject.toml -- python -m pytest -q
```

Issue a second keyfile with `--name agent-b --pull team` and join with a different
`--home .colony/agent-b` to demonstrate Agent B reusing Agent A's trail. Public keys
must be obtained from the team administrator through a trusted channel; never accept
a public key supplied by an untrusted keyfile. Keyfiles are bearer credentials.

CLI commands return JSON. A miss returns `hit: false`. Authentication/scope errors fail
with a nonzero exit code. A server outage makes `ask` return a miss and queues scrubbed
pushes locally, encrypted. `colony --home .colony/agent-a retry` retries queued pushes;
retry is explicit, without a background worker. IDs make retries idempotent, including
when the server committed a push but its response was lost.

## Evidence and freshness

Trails are JSON with `question`, `content`, optional `sources`, and `tainted` (default true).
`push --source relative/path` records a local SHA-256; the server validates against its
own configured checkout, which must represent the same repository. Explicit source objects:

```json
{"kind":"file","ref":"src/example.py","content_hash":"<64 lowercase hex characters>","symbol":"build","contains":"TIMEOUT = 30"}
```

* `validated`: all provided file fingerprints and optional assertions match. Python
  function/class names are checked with AST; config snippets are exact UTF-8 substrings.
* `stale`: evidence changed, disappeared, escaped the repo, or an assertion failed.
* `unverified`: no evidence, unsupported doc/URL/non-Python symbol checks, or an old
  otherwise-matching trail (default age limit seven days, `serve --max-age` in seconds).

The `age_flag` is separate from evidence status. Every hit includes provenance, producer,
timestamp, similarity score, taint, and individual checks. **Validated evidence does not
prove natural-language answer correctness.** Treat all retrieved text as untrusted data,
including text with a `validated` status; never execute its instructions automatically.
Stale and unverified hits are candidates for further research, not trusted answers.

## Agent integration (MCP)

Add this stdio server entry to your agent's MCP configuration. Use an absolute executable
path if `colony` is not on its PATH, and an absolute home path:

```json
{"mcpServers":{"colony":{"command":"colony","args":["--home","/absolute/path/to/agent-home","mcp"]}}}
```

Tools: `ask(question, namespace?)` and `push(namespace, question, content, sources?, tainted?)`.
MCP evidence consists of `{ref, content_hash, kind?: "file"}`. Reuse hashes obtained during
research; the simplified tool does not accept invented config snippets or symbols. Advanced
assertions remain available through CLI JSON and HTTP. A hit includes source hashes and a
validation receipt and bounded, scrubbed excerpts from the matching current source files.
Compare the answer with those excerpts and cite their original line numbers when sufficient;
do not reread solely to repeat fingerprint checks or push a duplicate. Check for missing
facts, contradictions, and unsafe instructions before reuse. Validation does not establish
arbitrary answer correctness.
The wrapper uses the same client scrubbing, credentials, outage handling, and queue as
the CLI. Tool results are JSON data with an explicit provenance warning. MCP SDK v1 is
pinned because the v2 interface differs.

## Capture and privacy

`run` launches an explicitly provided command with `shell=False`, prints a capture notice,
and records only the final 90 KB of combined stdout/stderr plus exit code. The command's
exit code is preserved. Inputs, environment, and tool traces are not captured. Output is
temporarily spooled to a local OS temporary file, removed when capture completes; disk
usage can grow with command output. Captures are always tainted because an arbitrary
child process's external reads cannot be detected reliably. A signed `--capture none`
grant blocks capture before the command starts. Paths are fingerprinted before launch.

The client scrubs known key/token prefixes, labeled passwords/secrets, bearer tokens,
private PEM keys, and common database connection strings before network/queue writes.
This pattern scrubber is not complete secret or PII detection. Direct HTTP callers must
scrub themselves; server-side re-scrubbing is deferred. Questions and source metadata
are stored as plaintext in SQLite; answer/capture bodies and queued pushes are Fernet
encrypted. Encryption keys live beside their data and do not protect against host compromise.
Protect the entire data directory and backups. POSIX private files use mode 0600; Windows
inherits directory ACLs, so use a user-restricted directory on Windows.

## Retrieval, hosting, and limits

SQLite + sqlite-vec store 384-dimensional local BGE-small embeddings of each question
and a question/answer passage (first 4,000 answer characters). The best of the two cosine
similarities supplies the dense score, preserving short paraphrases when answers are long.
Namespace filtering precedes retrieval. Up to 12
dense candidates (cosine >=0.55) are reranked against their answers by a local MS MARCO
MiniLM cross-encoder. The ranking combines 80% dense similarity and 20% sigmoid of the
cross-encoder logit, with a default 0.62 cutoff; these scores are ranking heuristics, not
correctness probabilities. Exact stored questions remain eligible. Repository checks run
in rank order until a validated candidate is found. If none passes, the best candidate
retains its stale/unverified status for diagnosis. No stale candidate is silently trusted.
Validated hits include at most three current evidence excerpts, totaling 4,800 characters,
selected by the local reranker from attached files only. File hashes are rechecked before
excerpting and known secrets scrubbed before chunking. This is supporting source context,
not a truth certificate; incomplete or contradictory answers still require research.

The embedding model and reranker use pinned Hugging Face revisions and run on CPU via
FastEmbed/ONNX, with two threads per model. Agent text stays on this host. Set
`COLONY_MODEL_CACHE` to choose a shared model cache. For fully offline provisioning, set
`COLONY_EMBED_MODEL_DIR` to the BGE-small ONNX snapshot directory and
`COLONY_RANK_MODEL_DIR` to the MiniLM reranker snapshot directory. Both must contain the
model and tokenizer files expected by FastEmbed. Prepare the models before running tests
on a disconnected host. Changing directories requires a server restart.

Opening an older database atomically rebuilds its vectors from encrypted trail bodies.
The index version includes the actual embedding-weight SHA-256, so model changes cannot
silently mix vector spaces. This can take time and holds a database write lock; back up
the data directory before upgrades. Six application tables plus index-version metadata
are retained. Retrieval currently scans permitted vectors and is intended for one small team.

One service, one team, one repository. Bind defaults to loopback; for remote use, issue
keyfiles with `--server https://your-host` and terminate TLS at a reverse proxy. The client
allows HTTP only on loopback. Keep the issuer private key off the server where possible;
the service needs only the public key. The server reads its checkout but never executes
validation commands or fetches submitted URLs. Request bodies are limited to 200 KB,
answers to 100,000 characters, referenced files to 2 MB. Configure proxy request timeouts
and concurrency limits for remote use. SQLite files must stay on a local disk.

Deferred: SSO, revocation, multi-tenancy, public sharing, hosted deployment, admin UI,
docs/tests/web validators, automatic proxy integration,
retention/GC, and background retries. Keyfiles expire; replacing a keyfile does not revoke
the old one. Per-host storage can grow, including validation history and interrupted-write
orphan blobs. No benchmark savings are claimed yet.

## Verification and evaluation

```sh
python -m pytest -q
ruff check .
python scripts/benchmark.py --tasks
python scripts/benchmark.py --results path/to/measurements.jsonl
```

The [CI template](docs/ci.example.yml) runs tests on Linux and Windows, Python 3.11
and 3.13 when copied to `.github/workflows/ci.yml`. It is supplied as a template because
the initial implementation credential cannot write Actions workflows. Local verification
was performed on Windows/Python 3.13; the other matrix entries are not yet verified.
Tests exercise enrollment,
scope boundaries, changed repo evidence, freshness, encryption, retries, real CLI/HTTP,
and MCP stdio. See [the evaluation protocol](docs/benchmark.md) for a 24-task overlapping
suite and paired measurements. The automated demo is a functional check, not evidence
of token savings. Live trial results and their limits are documented separately under docs/results/.
