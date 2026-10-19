import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from colony.models import Grant, Keyfile


def canonical(grant: Grant) -> bytes:
    return json.dumps(grant.model_dump(), sort_keys=True, separators=(",", ":")).encode()


def issue(private_key: Path, grant: Grant) -> Keyfile:
    key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    return Keyfile(grant=grant, signature=base64.b64encode(key.sign(canonical(grant))).decode())


def verify(keyfile: Keyfile, public_key: Path) -> Grant:
    key = serialization.load_pem_public_key(public_key.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("Expected Ed25519 issuer")
    try:
        key.verify(base64.b64decode(keyfile.signature, validate=True), canonical(keyfile.grant))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Invalid keyfile signature") from exc
    if keyfile.grant.expires_at <= time.time():
        raise ValueError("Keyfile expired")
    return keyfile.grant


def token(keyfile: Keyfile) -> str:
    return base64.urlsafe_b64encode(keyfile.model_dump_json().encode()).decode()


def fingerprint(keyfile: Keyfile) -> str:
    return hashlib.sha256(token(keyfile).encode()).hexdigest()


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def generate_keys(directory: Path) -> None:
    key = Ed25519PrivateKey.generate()
    private_write(
        directory / "issuer.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    private_write(
        directory / "issuer.pub",
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


PATTERNS = [
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END (?:[A-Z ]*PRIVATE KEY)-----",
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^\s\"'<>]+",
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b",
    r"(?i)\b(?:bearer)\s+[A-Za-z0-9._~+/=-]{8,}",
    r"(?i)(?:[\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|client[_-]?secret)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
]


def scrub(text: str) -> str:
    for pattern in PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text)
    return text
