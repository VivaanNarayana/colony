import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet

from colony.models import Keyfile, Trail
from colony.security import fingerprint, private_write, scrub, token, verify


class Client:
    def __init__(self, home: Path | None = None):
        self.home = home or Path(os.environ.get("COLONY_HOME", str(Path.home() / ".colony")))
        self.keyfile = Keyfile.model_validate_json((self.home / "identity.json").read_text())
        verify(self.keyfile, self.home / "issuer.pub")
        self.url = self.keyfile.grant.server.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token(self.keyfile)}"}

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

    def ask(
        self, question: str, namespace: str | None = None, *, include_evidence_files: bool = False
    ):
        try:
            payload = {"question": scrub(question), "namespace": namespace}
            if include_evidence_files:
                payload["include_evidence_files"] = True
            return self.request("/ask", payload)
        except httpx.TransportError:
            return {"hit": False, "reason": "server_unavailable"}

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

    def push(self, trail: Trail):
        trail = self.sanitized(trail)
        try:
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
                temporary.unlink(missing_ok=True)
            previous = json.loads(
                Fernet((self.home / "queue.key").read_bytes()).decrypt(path.read_bytes())
            )
            if previous != payload:
                raise ValueError("Queued trail ID conflict") from None
            return {"id": trail.id, "stored": False, "queued": True}

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
                self.request("/trails", trail.model_dump())
            except httpx.TransportError:
                return {"sent": sent, "pending": True}
            path.unlink()
            sent += 1
        return {"sent": sent, "pending": False}
