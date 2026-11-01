# Colony

Shared memory for one team's coding agents. Push a learned answer, ask before repeating
research, and check stored repository evidence on every pull. Self-hosted CLI + FastAPI;
no model API key, inference service, or telemetry required. Local retrieval models download
on the first server start (about 160 MB); provision them before disconnecting the host.

## Guided installation and onboarding

Install a wheel into a dedicated Python environment. To build one from the selected
checkout, run `python -m pip wheel --no-deps --wheel-dir dist .`, then
`python -m pip install dist/colony_memory-0.1.0-py3-none-any.whl`. Installation resolves
runtime dependencies; development extras are unnecessary. See [INSTALL.md](INSTALL.md)
for fresh-environment commands and wheel/source-distribution verification.

Choose **a new local team** or **an existing server**. All onboarding paths must be
absolute, have an existing parent, and be outside the project holding shared evidence.
Quote paths with spaces. On Windows use absolute Windows paths in these examples.

For a new local team, preview and then prepare a dedicated private workspace:

```sh
colony onboard init --workspace /absolute/private/team --repo /absolute/project --ns team --dry-run
colony onboard init --workspace /absolute/private/team --repo /absolute/project --ns team --configure-codex
```

The result includes copyable `commands.prepare_models`, `commands.serve_foreground`,
and `commands.check_client`. Run model preparation explicitly, start the server command
in a foreground terminal, then check the client from another terminal. No daemon is
installed. Bootstrap creates a private issuer and two prepared homes: `agent-a` can
publish/pull, while `agent-b` can pull only; both disable command-output capture.
Prepared identities have not yet authenticated against a running server. The first
authenticated request registers them through the existing server mechanism.

`--configure-codex` installs the existing reversible project setup for agent A only.
Use a fresh trusted Codex session to load it. Agent B can use CLI/manual read-only MCP;
one project has one managed Colony connection. Without this flag, project files remain
untouched. `--interactive` prompts for missing paths on a terminal; all options also
work noninteractively. The issuer stays private to the team administrator.

To join an existing server, obtain the signed invitation and issuer public key through
separate trusted channels, then name the new private home explicitly:

```sh
colony onboard join --agent-home /absolute/private/agent-home --keyfile /absolute/invitation.json --issuer /absolute/trusted-issuer.pub --dry-run
colony onboard join --agent-home /absolute/private/agent-home --keyfile /absolute/invitation.json --issuer /absolute/trusted-issuer.pub --repo /absolute/project --ns team --configure-codex
```

Add `--read-only` for a pull-only project connection. Enrollment validates the invitation
locally and uses the existing server join operation only when applying. Reusing a complete
matching home verifies it locally without claiming a fresh server handshake. Conflicting
homes and unowned workspaces are refused. Dry runs create no directories or credentials,
and do not contact the server. Optional project setup can fail after credentials are
prepared/enrolled: the result then has `ok: false`, exits nonzero, and preserves credentials
for retry rather than claiming remote membership was undone.

Interrupted setup preserves owned sibling staging such as `.team.colony-bootstrap` or
`.agent-home.colony-enrollment`. Rerun the same request to resume complete owned work;
if an issuer or credential set is incomplete/conflicting, retain it and use a new explicit
workspace/home. Receipts distinguish an initializing plan from completed prepared identities.
Existing manual `init`, `issue`, `join`, `serve`, and setup/uninstall commands remain available.

### Diagnostics and offline models

```sh
colony --home /absolute/private/agent-home doctor --repo /absolute/project --ns team
colony --home /absolute/private/agent-home doctor --server https://colony.example --ns team --format json
colony models fetch --dir /absolute/private/models
colony models check --dir /absolute/private/models
colony doctor --role server --models /absolute/private/models
colony serve --repo /absolute/project --data /absolute/private/server-data --issuer /absolute/private/issuer.pub --models /absolute/private/models --offline
```

Doctor reports package metadata, Python/runtime capability, a temporary in-memory SQLite
extension probe, local identity signature/expiry/scopes, selected project configuration,
and selected model artifacts. It opens no persistent store, performs no migrations, creates
no homes/queues, and downloads no models. Only an explicit `--server` makes a public health
request, without a bearer credential or redirects. Public health does not verify authenticated
namespace access, model execution, retrieval correctness, or Codex trust/loading.

`models fetch` is the explicit network operation. It downloads the existing pinned model
revisions (about 160 MB), verifies fixed byte sizes and SHA-256 hashes from the pinned upstream
artifacts, and publishes readiness only after verification. Owned partial downloads can be
retried; changed completed models and unowned destinations are refused. Separate unrelated
models are preserved. `models check` is read-only and exits nonzero for incomplete/invalid
models. Fetching models does not send questions, memories, credentials, or model-service calls.

With `--offline`, startup requires verified local snapshots and disables hub/network download
fallback. Supply `--models` or both `COLONY_EMBED_MODEL_DIR` and `COLONY_RANK_MODEL_DIR` pointing
to the verified snapshots. Existing `serve` without the new flags retains its previous model
loading behavior. Doctor checks artifacts without executing ONNX; use the demo to exercise
the actual engine and transports on the target Python/platform.

### First shared-memory hit

```sh
colony demo --workspace /absolute/private/new-demo --models /absolute/private/models --configure-codex --cleanup
```

The demo requires a new disposable directory. It creates a synthetic repository, private
issuer, isolated server store, and two agent homes; publishes a real fingerprint-backed
finding; reuses it through fresh CLI and read-only MCP clients; changes the source; and
requires the original trail's stale receipt. It runs local ONNX inference with no model
provider or paid agent sessions. The timeout is 90 seconds by default (10–300 allowed),
with bounded process teardown afterward.

The server is stopped before completion. Successful `--cleanup` removes only the known
owned fixture layout; unexpected files or linked paths cause cleanup refusal. Without
cleanup, or after a failure, the private workspace remains for inspection. Results contain
fixed step statuses and no credential contents. A failed check exits nonzero. This fixture
demonstrates transport/evidence behavior, not agent obedience or token/time savings.

## Git branch and commit awareness

Git-aware mode is opt-in. Assign the same explicit, nonsensitive repository ID to
the server and each trusted checkout of that project. Namespaces and signed grants
still control access; Git metadata is producer-reported provenance, never an
authorization or answer-correctness certificate. Repository identity is not inferred
from remote URLs, branch names, or absolute filesystem paths.

```sh
colony serve --repo /absolute/server-project --data /absolute/private/data --issuer /absolute/private/issuer.pub --repository-id project --offline
colony --home /absolute/private/agent-home setup codex install --repo /absolute/caller-project --ns team --repository-id project
colony --home /absolute/private/agent-home ask "Build timeout configuration" --ns team --repo /absolute/caller-project --repository-id project
colony --home /absolute/private/agent-home push finding.json --ns team --repo /absolute/caller-project --repository-id project --source settings.py
```

Use absolute Windows paths on Windows. The managed project connection forwards
`--workflow-repository-id project` with its existing workflow repository/namespace;
that option can also be supplied to a manually configured `colony mcp` connection.
Without the new ID options, existing CLI, MCP, setup and server behavior remains
unchanged. Use `--repository-id` with `run` or `memory correct` to record their
publication/correction provenance too. Git-aware requests require a server advertising
the same ID and protocol through authenticated `/capabilities`; older/disabled servers
produce an explicit unsupported lookup result or publication failure, never a silent
claim of Git protection. `/health` retains its original public contract.

For each Git-aware operation, Colony reads the local commit, branch or detached-HEAD
state, and dirty/untracked state. The collector has a three-second total deadline and
64 KiB aggregate output ceiling. It disables filesystem monitors, external clean/process
filters, optional index writes and lazy object fetching; it never fetches or switches
branches. Gitlinks/submodules, unborn/bare repositories, unsupported Git commands,
missing Git, oversized output, and inconsistent snapshots are conservatively unavailable.
Custom filters can conservatively make a worktree appear dirty. Known credential-shaped
branch names are unavailable, and sensitive-shaped repository IDs are refused without
echoing their contents. No remote URL, Git configuration contents, or local absolute path
is published in Git provenance.

- Matching repository ID, branch and commit with clean worktrees is eligible for saved
  answer reuse only if the existing file and lifecycle checks also pass.
- A different branch/commit, dirty/unknown context, or legacy finding with unknown origin
  withholds the saved answer. Current matching file evidence can still be returned.
- A different logical repository is excluded from both answers and evidence. A Git mismatch
  does not globally mark a memory stale for users on another branch.
- Detached checkouts compare explicit commit identities. Non-Git directories retain file
  validation with an explicit `not_applicable` Git receipt, without inventing a commit.

The caller rechecks source fingerprints and rebuilds bounded, scrubbed excerpts from its
own working tree without loading retrieval models. Server excerpts alone are not labeled
current caller reads. If its context changes during lookup, or its files do not match,
the client returns a safe miss. The server can use a different checkout; its independently
observed Git state is recorded separately and never substitutes for the producer's origin.
Git comparisons and file checks do not certify that an answer's claims are true.

Provenance is stored encrypted in an additive `trail_git_provenance_v1` table and exposed
through permission-checked memory inspection. Old records remain unknown; migration does
not backfill guessed origins. Missing metadata preserves legacy payload bytes and duplicate
IDs. Git-aware workflow findings include their context in their deterministic identity.
Encrypted offline queues freeze the original context: `retry` never restamps a finding
with the current branch. Corrections receive new origin metadata; restore preserves the
original origin. Following an unknown correction outcome, replay the identical request,
including its Git context, or inspect current state. Recollecting context after a branch
switch is a different request and can correctly conflict with the original operation ID.
The direct API accepts the frozen original `git_context` for an exact replay.

Back up the database, blobs and storage key together before upgrading. Older binaries
ignore Git provenance, so downgrading does not preserve Git-aware reuse protection.
Git-aware mode adds bounded local reads, Git subprocesses, capability requests and compact
tool receipts. It introduces no model-provider calls; its effect on research tokens and
end-to-end time is unmeasured, and no savings claim is made.

## Adaptive context budgets (experimental, opt-in)

Adaptive mode packs the existing selected evidence into a bounded response. It keeps
complete selected paragraphs and Markdown code blocks, including fenced and indented
commands and separate sections of the same file. Markdown blocks retain their heading
ancestry as original source text;
overlapping required spans are combined so separately selected facts remain present.
List or blockquote heading scopes conservatively retain the original source from the
start of the file through the selected block.
Unfenced shell files and files with a shebang require their entire source, since blank
lines alone cannot identify safe command boundaries. These conservative spans can
increase response size or require a budget-limited miss. Compact mode omits optional
surrounding section text even when more would fit. Explicit complete-file requests may
restore a whole file when it fits.
Retrieval models, ranking, permissions, source validation and Git compatibility rules
are unchanged. There is no extra summarizer, automatic retry or model-provider call.

```sh
colony --home /absolute/private/agent-home ask "Build timeout configuration" --ns team --repo /absolute/project --repository-id project --context-budget 2048
colony --home /absolute/private/agent-home setup codex install --repo /absolute/project --ns team --repository-id project --context-budget 2048
colony --home /absolute/private/agent-home mcp --workflow-namespace team --workflow-repo /absolute/project --context-budget 2048
```

`--context-budget` without a number selects 2048; explicit limits are integers from
256 through 8192. Values such as 512, 1024 and 2048 are starting points to evaluate,
not measured optimal profiles. The budget is a ceiling, not a target to fill.
Without this option, legacy responses, setup defaults and tool schemas stay unchanged.
Only adaptive MCP connections expose an optional `max_context_tokens` argument on
`ask`, allowing an explicit larger or smaller request with the connection's encoding.

The default `utf8_bytes` accounting counts UTF-8 bytes of the canonical JSON payload.
It is labelled `utf8_bytes_estimate`, not an exact model-token count or a universal
token bound. This conservative size-based mode works without an optional tokenizer.
For exact counts under the declared `o200k_base` encoding, install `colony-memory[budgets]`
and explicitly prepare the tokenizer on each server/client host that will count replies:

- Obtain the [official o200k_base data](https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken)
  outside request handling and keep it at an absolute local path.
- Verify SHA-256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
- Set `COLONY_CONTEXT_TOKENIZER` to that file and add `--context-encoding o200k_base`
  alongside `--context-budget`. Server and client paths may differ; the verified data
  and encoding must match. The optional package supports tiktoken 0.14.x.

The runtime reads at most 8 MiB, verifies the hash and constructs the encoding locally.
It never calls a downloading tokenizer loader. Missing, changed or unavailable requested
encoding data fails explicitly; it does not silently switch to estimates. The encoding
name is not inferred from an agent's model and does not establish provider billing counts.

HTTP clients send optional `context_budget` in `/ask`, for example
`{"max_tokens":2048,"encoding":"utf8_bytes"}`. Authenticated `/capabilities` advertises
the version, available encodings and supported limits. New clients refuse unsupported
servers without retrying an unbudgeted lookup. `/health` is unchanged.

Every adaptive response includes a `context_budget` receipt with `version`, `encoding`,
`max_tokens`, `used`, `measurement` and `limited`. `used` includes that receipt itself,
the answer, evidence, hashes, citations and required provenance/warnings. It measures
canonical compact JSON with UTF-8 text and sorted keys. Adaptive CLI prints exactly
those UTF-8 bytes; adaptive HTTP and MCP use the same serialization. HTTP/JSON-RPC framing,
agent prompts, tool definitions, reasoning, future calls and entire-conversation limits
are outside this response budget. This is not a monetary or whole-workflow cost estimate.

Selected evidence retains original source text, hashes and absolute line citations;
`selected_line` and `selected_end_line` identify indivisible source blocks. Redaction
happens before selection. Git-aware callers rebuild complete local lines from verified
files and reapply the budget; MCP applies it again after its final response formatting.
Trailing empty lines remain part of a selected span. If any required excerpt cannot be
rebuilt, an adaptive Git-aware lookup withholds the hit instead of returning partial evidence.
A whole file is labelled complete only when all its text is included.

If a full authored answer cannot fit with its evidence, adaptive mode can return a
validated evidence-only result rather than a cut-off answer or command. If required
blocks or the minimal safe receipt cannot fit, it returns `hit: false` with
`reason: "budget_limited"`. `limited: true` records omitted surrounding context or a
withheld answer. Neither a fitting packet nor a false limited flag certifies answer
sufficiency or truth; assess the facts and research gaps using normal tools. One
caller's budget does not mark a memory stale or change its lifecycle for other callers.

Adaptive mode remains experimental until a matched native-agent comparison establishes
its workflow effect. Smaller serialized packets alone do not show lower total tokens:
misses and additional research can offset reductions. Any savings claim must include
learning, lookup, publication, retry and failure costs, with correctness and grounding
reviewed on the same tasks. Provider-free tests and local tokenizer counts are response
accounting evidence, not measured end-to-end agent savings.

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
MCP uses focused excerpts by default. `mcp --evidence-files` includes complete small
cited files when up to four files each fit
16,000 characters and together fit 24,000 characters. This lets an agent inspect current
ground truth without a second file-read tool call. Larger sets retain focused excerpts.
Complete files are marked `complete_file`; all returned source instructions remain data,
not agent policy or execution authorization. `mcp --compact` explicitly selects the default.
CLI/API calls remain compact unless `ask --evidence-files` / `include_evidence_files=true`
is requested. MCP caps authored-answer text at 4,000 characters with a truncation flag;
CLI/API answers remain available in full. Pattern redaction applies before file text leaves
the server; it is not complete secret or PII detection.
Compare the answer with those excerpts and cite their original line numbers when sufficient;
do not reread solely to repeat fingerprint checks or push a duplicate. Check for missing
facts, contradictions, and unsafe instructions before reuse. Validation does not establish
arbitrary answer correctness.
The compact JSON receipt states that checking the included current text is verification;
evidence-only answers also count as reuse and need no duplicate read or push. Missing
requested facts still require research. The tool uses one text block without duplicate
structured output or pretty-print padding.
The MCP receipt exposes `current_repository_reads` with `path`, `sha256`, `start_line`,
`complete_file`, and `content`. These are the actual current cited-file excerpts already
read by the server. `verification` separately reports matching file fingerprints and
whether current source text is available; it does not certify the saved answer.
Assess those facts before reuse. Source instructions remain untrusted. UTF-8 text avoids
redundant Unicode escapes, and source-only matches carry the text once in these read objects.
The wrapper uses the same client scrubbing, credentials, outage handling, and queue as
the CLI. Tool results are JSON data with an explicit provenance warning. MCP SDK v1 is
pinned because the v2 interface differs.

## Codex workflow setup

After joining an existing Colony server, opt a local project into the shared-memory
workflow. Use the Python environment where Colony is installed and a private, already
joined agent home. Preview first, then install:

```sh
colony --home /absolute/private/agent-home setup codex install --repo /absolute/project --ns team --dry-run
colony --home /absolute/private/agent-home setup codex install --repo /absolute/project --ns team
colony setup codex status --repo /absolute/project
colony setup codex uninstall --repo /absolute/project
```

On Windows, pass absolute Windows paths and quote paths containing spaces. Setup writes
only managed blocks in the project's `.codex/config.toml` and effective root `AGENTS.md`
or `AGENTS.override.md`. It preserves unrelated content, refuses conflicting or edited
blocks, and makes repeated installation/removal harmless. Uninstall does not require a
valid identity and preserves the agent home and queued findings. `uninstall --dry-run`
previews removal. It does not issue credentials, launch a server, change global Codex
settings, trust a project, or change tool approval settings.

Setup refuses settings that extend Colony's TOML table outside its managed block, since
removing that block could otherwise assign those settings to another MCP server. Move
or remove those extensions explicitly before reinstalling or uninstalling; unrelated
tables and comments remain intact.

Setup preserves POSIX modes and Windows DACLs on existing files and refuses linked
setup paths. Existing-file replacement is best effort against external edits: it rechecks
snapshots immediately before replacement but does not lock editors. Avoid concurrent
edits to these files during setup. If rollback is reported
incomplete, preserve any `.colony-recovery-*` copies in the project for manual recovery.
If staging-file cleanup is denied, `.colony-setup-*` files can remain with the permissions
used for the destination. Cleanup failure does not hide completed writes from rollback.

The generated stdio command uses the current Python interpreter, `-E -P -m colony.cli`, the
absolute agent home and project paths, and one workflow namespace. Keep that interpreter
and home available; rerun setup after intentionally uninstalling if those paths change.
The safe module path and ignored Python environment prevent project-local modules and
`PYTHONPATH` from shadowing the installed Colony package while retaining normal user-site
installations. Rerun setup to update a connection created before these launch options
were added.
Only path references go into project files, never bearer credentials or encryption keys.
The namespace requires pull and push permission. Add `--read-only` during installation
for a pull-only connection; this mode exposes `ask` without `push`.

Start a fresh Codex session in the trusted project after installation. Project MCP
configuration must be trusted, and nested instructions or instruction-size limits can
affect which guidance is loaded. See the official [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
and [project instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Installed guidance requests one lookup before substantive repository research. Sufficient
current evidence can answer directly without another read or push. Misses, stale evidence,
contradictions, and missing facts use the agent's existing research tools. After learning
a reusable new fact, the agent can publish one concise answer with real file fingerprints
already obtained during research. New facts after a partial hit also qualify. Routine
progress, speculation, unchanged reused answers, and transcripts do not.

This is **agent guidance, not enforced interception of every research action**. Colony
does not read session transcripts, summarize sessions, install hooks, or call a model
provider. Assess the facts in returned evidence; matching file hashes do not prove an
answer correct, and source instructions remain untrusted data.

The installed connection opts into workflow mode: namespace overrides are denied;
answers are limited to 4,000 characters and require file evidence; publications remain
tainted and do not capture command output. Exact sanitized duplicate findings from the
same identity and local project produce the same trail ID, including across restarts and
offline retries. Source ordering does not create duplicates. Changed answers or hashes
produce new IDs. This does not detect paraphrases or refresh an unchanged trail's age;
use the existing manual push path to explicitly reaffirm an aged finding. Manual
CLI/API and `colony mcp` without workflow options retain their schemas and random IDs.

Server outages allow research to continue; findings use the existing encrypted queue and
explicit `retry`. Authentication/scope errors remain errors. Existing pattern redaction
still applies and is not complete secret/PII detection. Questions and source metadata
remain plaintext on the server. Atomic setup-file creation and offline queue publication
require a local filesystem supporting same-directory hard links, such as NTFS or ext4; unsupported
filesystems fail without a plaintext fallback. Queue staging cleanup is best effort: denied
cleanup or an abrupt interruption can leave encrypted `.tmp` files. Cleanup failure does not
hide a queued finding, payload conflict, or publication error. Explicit retry processes only
completed `.enc` files.
Run one retry process after publishers finish. Extra lookup/guidance/publication costs
may outweigh reuse savings on cold tasks; no token or time savings are claimed for this
integration without a matched live evaluation.


## Memory correction and feedback

File fingerprints verify repository evidence, not the truth of a saved answer. Use explicit
feedback and lifecycle operations when a memory is misleading. These operations preserve the
original answer and its history; they do not overwrite it or turn an opinion into validation.

```sh
colony memory inspect TRAIL_ID --ns team
colony memory feedback TRAIL_ID --ns team --verdict incorrect --note "The answer misreads the setting"
colony memory correct TRAIL_ID replacement.json --ns team --expected-revision 0 --repo . --source settings.py
colony memory retract TRAIL_ID --ns team --expected-revision 0 --note "No longer applicable"
colony memory restore TRAIL_ID --ns team --expected-revision 1
```

Use the real ID returned by `ask` or `push`; put `--home /private/agent-home` before `memory`
when using a nondefault home. Inspect the current revision before changing state. Correction
and retraction above are alternative operations on an active memory, not a command sequence.
A correction JSON file supplies `question`, `content`, and optional `sources`; `--source` adds
actual repository file fingerprints, as with normal CLI publication. Corrected content is
bounded to 4,000 characters and must cite current repository file evidence. MCP callers reuse
fingerprints already obtained during research. Matching fingerprints still do not establish
that the corrected prose is true.

- Inspection requires pull permission. Feedback requires pull and push permission in that
  namespace and can report `helpful`, `incorrect`, or `outdated` for another contributor's memory.
- Only the original producer, identified by the issuer-signed member ID, can correct, retract,
  or restore their memory, with both pull and push permission. Keep a valid credential with
  that original member ID; a newly issued member ID does not inherit ownership. This version
  has no moderator or cross-author acceptance role.
- Feedback keeps each member's latest verdict plus bounded inspection history. Reports remain
  unverified opinions: counts do not change ranking, evidence validation, or age. Flagged hits
  include feedback metadata so the agent can check the answer against current source evidence.
- Correction atomically creates a new answer and marks the original `superseded`. Retraction
  marks an active answer `retracted`. Both are excluded from answer retrieval and source-evidence
  fallback. Restore is explicit and applies only to a retracted answer; a superseded original
  cannot be restored into competition with its replacement.
- Lifecycle state and revision are separate from `validated`, `stale`, and `unverified` evidence
  status. Revision checks reject conflicting simultaneous changes. Normal pushes, delayed push
  retries, server restarts, and reindexing do not reactivate retired IDs or reset their age.

MCP adds `memory_inspect`, `memory_feedback`, `memory_correct`, and `memory_change` for manual
and writable workflow connections. Workflow namespace binding applies to every operation.
The existing read-only workflow connection continues to expose only `ask`; pull-only users can
inspect through the CLI or HTTP. The HTTP endpoints are `POST /memory/inspect`, `/memory/feedback`,
`/memory/correct`, and `/memory/change`. Feedback and history notes are bounded to 1,000 characters,
scrubbed, and encrypted at rest using the existing storage key. History inspection is bounded.

Replay/conflict fingerprints use domain-separated HMAC-SHA256 with a private key derived from
`storage.key`; no unkeyed request digest is written for new operations. Schema version 2
atomically key-wraps existing schema-1 replay digests without decrypting notes, changing
operation IDs, or losing prior results. Historical schema-1 database files, free pages,
WAL/journals, or backups may retain the old guessable verifiers: this upgrade changes live
records and does not provide forensic erasure. Keep those historical files protected.

Feedback notes and saved content remain untrusted data. Inspection marks question/source
metadata when display truncation is necessary after redaction; original stored values remain
unchanged. Existing question/source metadata storage and pattern-redaction limitations still apply.

These new mutations are online-only and do not enter the offline push queue. A transport
timeout has an **unknown outcome**, not a confirmed failure or successful queue. Keep the
reported operation ID and replay the identical request with `--operation-id`, or inspect
current state first. A replay acknowledges the original operation without applying it again;
its `replayed` flag and current memory metadata distinguish it from a new state change.
Changing a request while reusing an operation ID is a conflict. Existing encrypted push/retry
behavior is unchanged.

Before upgrading an existing server, stop it and back up its database, encrypted blobs, and
`storage.key` together. Startup adds versioned lifecycle/feedback tables without changing old
trail payloads or signed credentials. Test recovery on a copy first. Downgrading the binary
alone is unsafe: older versions ignore lifecycle records and can return retired answers.
Rollback requires the matching pre-upgrade database, blobs, and key with the old binary;
in-place downgrade is unsupported. No token savings or improved answer correctness is claimed
without measured, independently reviewed evidence.

## Local usage and savings reports

Usage collection is **off by default**. Opt in for one local client home; no server changes,
provider calls, prompt changes or outbound analytics are involved. These commands also work
before joining a server:

```sh
colony --home .colony/agent-a usage status
colony --home .colony/agent-a usage enable --retention-days 30 --max-events 10000
colony --home .colony/agent-a report
colony --home .colony/agent-a report --since 2026-10-01 --until 2026-11-01 --format json --output usage.json
colony --home .colony/agent-a usage disable
```

Once enabled, official CLI/MCP client calls record fixed outcome categories, timestamps,
durations, random event IDs and private keyed logical-operation identifiers. Queued publication
is distinct from confirmed acceptance; explicit retry attempts are counted individually and
linked to the same publication. Memory-operation replays share an identifier when they use the
same operation ID. No questions, answers, source paths, namespace names, identities, credentials
or exception messages enter the journal. Existing responses and MCP tool schemas are unchanged.
Direct HTTP users, other homes, disabled periods and calls lost to a process crash are outside
this local view; counts describe retained observations, not a complete team history.

The report separates answer hits, evidence-only hits, misses, stale/unverified results,
outages and errors. A hit or a helpful-memory vote does not prove useful reuse or correctness.
JSON reports include up to 50 recent lookup IDs for explicit local assessments:

```sh
colony --home .colony/agent-a report --format json
colony --home .colony/agent-a usage review <lookup-event-id> --verdict useful
```

Verdicts are `useful`, `incorrect` or `irrelevant`. They apply to eligible observed hits,
are self-reported, and do not change shared memory feedback, rankings or evidence validation.
Reports show assessed/unassessed counts and denominators; selectively reviewed hits do not
establish overall answer quality. Date bounds are inclusive `--since`, exclusive `--until`;
dates mean UTC midnight, and timestamps require `Z` or an explicit timezone offset.
Latency covers the client operation before its journal write, including failed operations;
it excludes journal-write overhead and is not full workflow wall time. p95 requires at least
20 observations. Enabled recording authenticates retained records and grows with journal size;
collection adds local CPU/disk work even though it adds no model calls.

Records are encrypted under a separate private key in `<home>/usage`, alongside a versioned
SQLite journal. Limits are 1–365 retention days and 1–10,000 events; the database is capped at
8 MiB, with a transient rollback journal potentially adding about another 8 MiB. Oldest events
are evicted at capacity. Expired events are excluded from reads and removed on the next
successful write or purge; retention is not a background deletion service. Reports expose
retention/capacity removals and collection gaps. Short lock waits can drop observations under
contention; fixed warnings and sticky health markers disclose failures while the original
operation result/error is preserved. If the filesystem cannot retain a failure marker, health
may be unknown. Symlink/reparse paths in the usage area and its ancestors are refused.

`usage disable` keeps existing data available for reporting. `usage purge` explicitly deletes
local events and their assessments, preserving settings and collection health history; it does
not delete shared memories, queued pushes or imported source logs. Encryption protects a copied
journal without its key, not a compromised account holding both files. File sizes/settings and
report aggregates still reveal activity; secure deletion of historical copies is not promised.

Token totals and savings remain **unknown** in an ordinary usage report. To import measured
evidence, explicitly select a compatible frozen Colony/no-Colony manifest and native-log
inventory using the existing `colony.product-value/1` format:

```sh
colony report compare --manifest frozen-evidence.json --logs native-logs --format json --output comparison.json
```

This reads only the selected supported logs locally and writes a metadata-only
`colony.usage-comparison/1` summary. It does not search transcripts, retain their text, launch
agents or accumulate duplicate imports. Input, cached input, output, gross and uncached tokens
include learning, correction, retry and failure costs from that inventory. Missing or failed
usage keeps complete totals/savings unknown while known subtotals remain visible. Compatible
matched tasks, model/budget identities and externally supplied correctness/grounding reviews
are required; hashes bind evidence, not its truth. Negative savings, unknown quality and citation
gaps remain visible. No dollar savings, confidence-supported product benefit or production
readiness is inferred. The historical `scripts/product_value.py` entry point and full scorecard
format remain available; unlike the new summary, that explicit legacy output retains protocol
text and must be handled accordingly. Historical trials are not current-version measurements.

## Capture and privacy

`run` launches an explicitly provided command with `shell=False`, prints a capture notice,
and records only the final 90 KB of combined stdout/stderr plus exit code. The command's
exit code is preserved. Inputs, environment, and tool traces are not captured. Output is
temporarily spooled to a local OS temporary file. Secret patterns are matched against
the complete spool before overlapping secret bytes are removed from the retained tail;
truncation cannot hide their opening markers. Unterminated private-key blocks are
redacted through the end of output. The file is removed when capture completes; disk
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
MiniLM cross-encoder. The eligibility score combines 80% dense similarity and 20% sigmoid of the
cross-encoder logit, with a default 0.60 cutoff; these scores are ranking heuristics, not
correctness probabilities. Exact stored questions remain eligible. Repository checks run
in rank order until a validated candidate is found. If none passes, the best candidate
retains its stale/unverified status for diagnosis. No stale candidate is silently trusted.
Compact validated hits include at most two current evidence excerpts, totaling 2,800 characters,
selected by the local reranker from attached files only. File hashes are rechecked before
excerpting and known secrets scrubbed before chunking. This is supporting source context,
not a truth certificate; incomplete or contradictory answers still require research.
For a saved answer, excerpt selection also uses its specific claims, weighted alongside
the question and reranker. This grounds a concrete command or behavior instead of merely
returning a passage about the same broad topic. The agent still checks relevance and truth.

When no validated saved answer matches, Colony can reuse evidence from an earlier trail.
On push it indexes bounded, scrubbed passages from the cited files whose hashes match
the server checkout. It never crawls uncited files or initializes memory from the repo.
Evidence retrieval merges 24 dense candidates (cosine >=0.45) with 24 lexical candidates
(query-term coverage >=0.15), rejects reranker logits below -5, and ranks paragraphs
with 40% dense similarity, 20% bounded reranker signal and 40% query-term coverage
(weighted by inverse document frequency). Repeated citations of the same passage are
grouped before selecting at most 48 distinct candidates, retaining alternative
trail origins when assertions fail. This avoids duplicate passages crowding out evidence,
without
treating a ranking score as answer confidence. Selected passages retain their identity
through excerpt expansion into their Markdown section or nearby source lines;
all attached sources and the age limit must still pass on pull. `match_kind: "answer"`
identifies a saved answer; `match_kind: "evidence"` identifies supporting source context
with no matching saved answer. Agents must read that context and assess whether it
answers the question. This broadens reuse beyond paraphrases of stored questions.
Paragraph ranking includes its Markdown heading hierarchy; example `--question` values
are masked for ranking so echoing a query cannot displace the section that answers it.
The response can include two distinct sections from the same validated trail's cited
files when their ranking scores are within 0.15, retaining ranking order and the existing
2,800-character cap. Displayed text is always the original scrubbed file text with its
original line numbers, without synthetic ranking headings or masked question values.
Procedural requests require command or instruction cues; capability overviews alone do
not qualify. Weak evidence also requires at least 50% weighted query-term coverage or
a nonnegative reranker logit. For procedural requests, command/instruction passages with
logits at least -2 can pass without literal query-word coverage; short commands often
answer the question without repeating its words. Saved answers must also have at least
0.70 semantic question-to-question similarity for procedures, or 0.65 for descriptions
(exact questions remain eligible), limiting answer-vector
topic drift while retaining paraphrases. Exact records retain their diagnostic/capture content.
Eligible answers are ordered by raw reranker logits, with fused scores as a tiebreaker.
This preserves differences between strong positive matches that sigmoid saturation lost.
These are conservative retrieval heuristics, not answerability guarantees.
When expansion exceeds the remaining budget, excerpts anchor at the selected paragraph
so its relevant text survives trimming. A memory result does not determine other tools'
availability; incomplete hits and misses still require configured research tools.
Lexical selection rereads scoped, cited files with matching hashes; this bounded-file
approach is intended for small teams and does not provide a scalable full-text index.

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
the data directory before upgrades. Source-passage metadata and vectors are stored alongside
the original application tables; excerpt text is reread from matching files, not stored in
plaintext. Retrieval currently scans permitted vectors and is intended for one small team.

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
orphan blobs. The [GPT-6 Luna low regression benchmark](docs/results/luna-low/README.md)
measured 7.9% net token savings with 24/24 correct answers in both arms; production
savings remain unproven, and task wall time was slower.

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
