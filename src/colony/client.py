import json
import os
import re
import sys
import time
import uuid
from contextlib import suppress
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet

from colony.memory import MemoryConflict, MemoryForbidden, MemoryInvalid, MemoryNotFound
from colony.models import (
    GitContext,
    Keyfile,
    MemoryChange,
    MemoryCorrection,
    MemoryFeedback,
    MemoryInspect,
    Source,
    Trail,
)
from colony.security import fingerprint, private_write, scrub, token, verify
from colony.usage import UsageJournal


def _usage_outcome(operation, result, error):
    """Use only fixed classifications; never record payloads or exception messages."""
    if error is not None:
        if isinstance(error, MemoryForbidden) or (
            isinstance(error, ValueError)
            and error.args in (("Namespace push denied",), ("Output capture denied",))
        ):
            return "denied"
        if isinstance(error, MemoryConflict):
            return "conflict"
        if isinstance(error, MemoryNotFound):
            return "not_found"
        if isinstance(error, MemoryUnavailable):
            return "unavailable" if operation == "inspect" else "unknown"
        if isinstance(error, httpx.HTTPStatusError):
            return {
                401: "denied",
                403: "denied",
                404: "not_found",
                409: "conflict",
                422: "invalid",
            }.get(error.response.status_code, "error")
        if isinstance(error, httpx.TransportError):
            return "unavailable"
        return "invalid" if isinstance(error, ValueError) else "error"
    if not isinstance(result, dict):
        return "unknown"
    if operation == "lookup":
        if result.get("reason") == "server_unavailable":
            return "unavailable"
        status = result.get("status")
        if status is not None and not isinstance(status, str):
            return "unknown"
        if status in ("stale", "unverified"):
            return status
        if result.get("hit") is True:
            match = result.get("match_kind")
            if match not in (None, "answer", "evidence"):
                return "unknown"
            return "evidence" if match == "evidence" else "answer"
        return "miss" if result.get("hit") is False else "unknown"
    if operation in {"publish", "retry"}:
        if result.get("queued") is True:
            return "queued"
        return "accepted" if result.get("stored") is True else "unknown"
    return "accepted"


def _observed(operation):
    """Observe official client calls without changing their values or error behavior."""

    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            started = time.perf_counter()
            result, error = None, None
            try:
                result = function(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = exc
                raise
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                try:
                    observed_operation = operation
                    logical_id = None
                    if operation in {"publish", "retry"}:
                        trail = args[0] if args else kwargs.get("trail")
                        logical_id = "trail:" + trail.id if isinstance(trail, Trail) else None
                    elif operation not in {"lookup", "inspect"}:
                        if operation == "change":
                            action = kwargs.get("action")
                            if isinstance(action, str) and action in {"retract", "restore"}:
                                observed_operation = action
                        operation_id = kwargs.get("operation_id")
                        if isinstance(result, dict):
                            operation_id = result.get("operation_id", operation_id)
                        if isinstance(error, MemoryUnavailable):
                            operation_id = error.operation_id
                        if isinstance(operation_id, str) and re.fullmatch(
                            r"[a-f0-9]{32}", operation_id
                        ):
                            logical_id = "memory:" + operation_id
                    self.usage.record(
                        operation=observed_operation,
                        outcome=_usage_outcome(observed_operation, result, error),
                        duration_ms=elapsed,
                        logical_id=logical_id or "request:" + uuid.uuid4().hex,
                    )
                except Exception:
                    # Logging is optional and must not replace a product result/error.
                    with suppress(Exception):
                        self.usage.note_gap()
                    with suppress(Exception):
                        print(
                            "Colony usage: collection failed; usage may be incomplete.",
                            file=sys.stderr,
                        )

        return wrapped

    return decorate


def _memory_acknowledged(result, request: dict, *, mutation: bool) -> bool:
    """Validate known acknowledgment fields without rejecting additive server metadata."""

    def identifier(value):
        return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None

    def counter(value):
        return type(value) is int and value >= 0

    if not isinstance(result, dict) or result.get("id") != request["trail_id"]:
        return False
    memory = result.get("memory")
    if not isinstance(memory, dict) or memory.get("state") not in (
        "active",
        "retracted",
        "superseded",
    ):
        return False
    if not counter(memory.get("revision")) or "replacement_id" not in memory:
        return False
    if memory["replacement_id"] is not None and not identifier(memory["replacement_id"]):
        return False
    feedback = memory.get("feedback")
    if not isinstance(feedback, dict) or not all(
        counter(feedback.get(k)) for k in ("helpful", "incorrect", "outdated")
    ):
        return False
    if mutation:
        if (
            result.get("operation_id") != request["operation_id"]
            or type(result.get("replayed")) is not bool
        ):
            return False
        for field in ("action", "verdict"):
            if field in request and result.get(field) != request[field]:
                return False
        if "question" in request:
            replacement = result.get("replacement_id")
            if not identifier(replacement) or replacement == request["trail_id"]:
                return False
        return True
    if result.get("namespace") != request["namespace"]:
        return False
    for field, maximum in (("producer_id", 100), ("question", 2000), ("content", 4000)):
        if not isinstance(result.get(field), str) or not 1 <= len(result[field]) <= maximum:
            return False
    if type(result.get("content_truncated")) is not bool:
        return False
    sources, history = result.get("sources"), result.get("history")
    if (
        not isinstance(sources, list)
        or len(sources) > 50
        or not isinstance(history, list)
        or len(history) > 20
    ):
        return False
    try:
        for source in sources:
            if not isinstance(source, dict):
                return False
            Source.model_validate(
                {key: value for key, value in source.items() if key in Source.model_fields}
            )
        return all(
            isinstance(event, dict)
            and isinstance(event.get("note"), str)
            and len(event["note"]) <= 1000
            for event in history
        )
    except (TypeError, ValueError):
        return False


class MemoryUnavailable(ValueError):
    """No mutation acknowledgment; retain the operation ID for an explicit identical replay."""

    def __init__(self, operation_id: str | None = None):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            operation_id = None
        self.operation_id = operation_id
        message = "Memory service unavailable"
        if operation_id is not None:
            message += (
                "; mutation outcome unknown. Replay the same request with operation_id="
                + operation_id
            )
        super().__init__(message)


class Client:
    def __init__(self, home: Path | None = None):
        self.home = home or Path(os.environ.get("COLONY_HOME", str(Path.home() / ".colony")))
        self.keyfile = Keyfile.model_validate_json((self.home / "identity.json").read_text())
        verify(self.keyfile, self.home / "issuer.pub")
        self.url = self.keyfile.grant.server.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token(self.keyfile)}"}
        self.usage = UsageJournal(self.home)

    @staticmethod
    def join(home: Path, keyfile: Path, issuer: Path):
        key = Keyfile.model_validate_json(keyfile.read_text())
        grant = verify(key, issuer)
        parsed = urlparse(grant.server)
        if (
            (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Server must use HTTPS, or loopback HTTP")
        # Verify server enrollment before persisting credentials. No redirects with bearer secrets.
        response = httpx.post(
            grant.server.rstrip("/") + "/join",
            headers={"Authorization": f"Bearer {token(key)}"},
            timeout=5,
        )
        response.raise_for_status()
        if (home / "identity.json").exists():
            raise ValueError("Already joined; use a separate COLONY_HOME for another identity")
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(home / "issuer.pub", issuer.read_bytes())
        private_write(home / "queue.key", Fernet.generate_key())
        private_write(home / "identity.json", key.model_dump_json().encode())
        return response.json()

    def request(self, route: str, payload: dict):
        response = httpx.post(self.url + route, json=payload, headers=self.headers, timeout=5)
        if response.status_code >= 500:
            raise httpx.ConnectError("Colony unavailable")
        response.raise_for_status()
        return response.json()

    def _git_supported(self, context: GitContext):
        try:
            capability = self.request("/capabilities", {})["git_context"]
            if capability["version"] != context.version or (
                capability["repository_id"] != context.repository_id
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError, httpx.HTTPStatusError):
            raise ValueError(
                "Server does not support the configured Git repository context"
            ) from None

    @_observed("lookup")
    def ask(
        self,
        question: str,
        namespace: str | None = None,
        *,
        include_evidence_files: bool = False,
        repo: Path | None = None,
        repository_id: str | None = None,
    ):
        context = None
        if repository_id is not None:
            from colony.git_context import capture

            if repo is None:
                raise ValueError("Git-aware lookup requires an explicit repository")
            context = capture(repo, repository_id)
        try:
            payload = {"question": scrub(question), "namespace": namespace}
            if include_evidence_files:
                payload["include_evidence_files"] = True
            if context is not None:
                try:
                    self._git_supported(context)
                except ValueError:
                    return {"hit": False, "reason": "git_context_unsupported"}
                payload["git_context"] = context.model_dump()
            result = self.request("/ask", payload)
            if context is not None:
                from colony.git_client import verify_lookup

                return verify_lookup(result, repo, context, whole_files=include_evidence_files)
            return result
        except httpx.TransportError:
            return {"hit": False, "reason": "server_unavailable"}

    def _memory_request(self, route: str, request_type, values: dict, *, mutation: bool):
        try:
            request = request_type.model_validate(values)
            data = request.model_dump()
            for field in ("question", "content", "note"):
                if field in data:
                    data[field] = scrub(data[field])
            for source in data.get("sources", []):
                for field in ("ref", "symbol", "contains"):
                    if source.get(field) is not None:
                        source[field] = scrub(source[field])
            data = request_type.model_validate(data).model_dump()
        except (TypeError, ValueError):
            raise MemoryInvalid() from None
        scopes = self.keyfile.grant.scopes
        if not any(s.namespace == data["namespace"] and s.pull for s in scopes) or (
            mutation and not any(s.namespace == data["namespace"] and s.push for s in scopes)
        ):
            raise MemoryForbidden()
        try:
            if isinstance(request, MemoryCorrection) and request.git_context is not None:
                self._git_supported(request.git_context)
            result = self.request(route, data)
        except httpx.TransportError:
            raise MemoryUnavailable(data.get("operation_id") if mutation else None) from None
        except httpx.HTTPStatusError as exc:
            error_type = {
                401: MemoryForbidden,
                403: MemoryForbidden,
                404: MemoryNotFound,
                409: MemoryConflict,
                422: MemoryInvalid,
            }.get(exc.response.status_code, MemoryInvalid)
            raise error_type() from None
        except ValueError:
            raise MemoryUnavailable(data.get("operation_id") if mutation else None) from None
        if not _memory_acknowledged(result, data, mutation=mutation):
            raise MemoryUnavailable(data.get("operation_id") if mutation else None) from None
        return result

    @_observed("inspect")
    def memory_inspect(self, trail_id: str, namespace: str):
        return self._memory_request(
            "/memory/inspect",
            MemoryInspect,
            {"trail_id": trail_id, "namespace": namespace},
            mutation=False,
        )

    @_observed("feedback")
    def memory_feedback(
        self,
        trail_id: str,
        namespace: str,
        *,
        verdict: str,
        note: str = "",
        operation_id: str | None = None,
    ):
        return self._memory_request(
            "/memory/feedback",
            MemoryFeedback,
            {
                "trail_id": trail_id,
                "namespace": namespace,
                "verdict": verdict,
                "note": note,
                "operation_id": uuid.uuid4().hex if operation_id is None else operation_id,
            },
            mutation=True,
        )

    @_observed("correct")
    def memory_correct(
        self,
        trail_id: str,
        namespace: str,
        *,
        expected_revision: int,
        question: str,
        content: str,
        sources: list[Source],
        note: str = "",
        operation_id: str | None = None,
        git_context: GitContext | None = None,
    ):
        return self._memory_request(
            "/memory/correct",
            MemoryCorrection,
            {
                "trail_id": trail_id,
                "namespace": namespace,
                "expected_revision": expected_revision,
                "question": question,
                "content": content,
                "sources": sources,
                "note": note,
                "operation_id": uuid.uuid4().hex if operation_id is None else operation_id,
                **({"git_context": git_context} if git_context is not None else {}),
            },
            mutation=True,
        )

    @_observed("change")
    def memory_change(
        self,
        trail_id: str,
        namespace: str,
        *,
        expected_revision: int,
        action: str,
        note: str = "",
        operation_id: str | None = None,
    ):
        return self._memory_request(
            "/memory/change",
            MemoryChange,
            {
                "trail_id": trail_id,
                "namespace": namespace,
                "expected_revision": expected_revision,
                "action": action,
                "note": note,
                "operation_id": uuid.uuid4().hex if operation_id is None else operation_id,
            },
            mutation=True,
        )

    def sanitized(self, trail: Trail) -> Trail:
        if not any(s.namespace == trail.namespace and s.push for s in self.keyfile.grant.scopes):
            raise ValueError("Namespace push denied")
        if trail.capture == "outputs" and self.keyfile.grant.capture != "outputs":
            raise ValueError("Output capture denied")
        data = trail.model_dump()
        data["question"], data["content"] = scrub(trail.question), scrub(trail.content)
        for source in data["sources"]:
            source["ref"] = scrub(source["ref"])
            for field in ("contains", "symbol"):
                if source[field] is not None:
                    source[field] = scrub(source[field])
        return Trail.model_validate(data)

    @_observed("publish")
    def push(self, trail: Trail, *, repo: Path | None = None, repository_id: str | None = None):
        if repository_id is not None:
            from colony.git_client import checked_sources
            from colony.git_context import capture

            if repo is None:
                raise ValueError("Git-aware publication requires an explicit repository")
            context = capture(repo, repository_id)
            if trail.git_context is not None and trail.git_context != context:
                raise ValueError("Git context changed before publication")
            checked_sources(repo, trail.sources)
            if capture(repo, repository_id) != context:
                raise ValueError("Git context changed before publication")
            trail = trail.model_copy(update={"git_context": context})
        trail = self.sanitized(trail)
        try:
            if trail.git_context is not None:
                self._git_supported(trail.git_context)
            return self.request("/trails", trail.model_dump())
        except httpx.TransportError:
            queue = self.home / "queue"
            queue.mkdir(exist_ok=True, mode=0o700)
            payload = {"identity": fingerprint(self.keyfile), "trail": trail.model_dump()}
            encrypted = Fernet((self.home / "queue.key").read_bytes()).encrypt(
                json.dumps(payload).encode()
            )
            path = queue / f"{trail.id}.enc"
            temporary = queue / f".{uuid.uuid4().hex}.tmp"
            try:
                private_write(temporary, encrypted)
                with suppress(FileExistsError):
                    # Publish complete ciphertext without replacing an existing envelope.
                    # Same-directory hard links are atomic on supported local filesystems.
                    os.link(temporary, path)
            finally:
                # Staging cleanup must not hide a durable queue entry or the primary error.
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            previous = json.loads(
                Fernet((self.home / "queue.key").read_bytes()).decrypt(path.read_bytes())
            )
            if previous != payload:
                raise ValueError("Queued trail ID conflict") from None
            return {"id": trail.id, "stored": False, "queued": True}

    @_observed("retry")
    def _retry_request(self, trail: Trail):
        if trail.git_context is not None:
            self._git_supported(trail.git_context)
        return self.request("/trails", trail.model_dump())

    def retry(self):
        sent = 0
        for path in sorted((self.home / "queue").glob("*.enc")):
            payload = json.loads(
                Fernet((self.home / "queue.key").read_bytes()).decrypt(path.read_bytes())
            )
            if payload["identity"] != fingerprint(self.keyfile):
                raise ValueError("Queued trail belongs to another identity")
            trail = self.sanitized(Trail.model_validate(payload["trail"]))
            try:
                self._retry_request(trail)
            except httpx.TransportError:
                return {"sent": sent, "pending": True}
            path.unlink()
            sent += 1
        return {"sent": sent, "pending": False}
