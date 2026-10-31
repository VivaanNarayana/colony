"""Shared online memory-operation contracts; error strings never include user content."""

import hashlib
import hmac
import json
import re

from colony.models import Grant, MemoryChange, MemoryCorrection, MemoryFeedback, Source
from colony.security import scrub


class MemoryNotFound(ValueError):
    def __init__(self):
        super().__init__("Memory not found or unavailable")


class MemoryConflict(ValueError):
    def __init__(self):
        super().__init__("Memory operation conflicts with current state or operation ID")


class MemoryForbidden(ValueError):
    def __init__(self):
        super().__init__("Memory operation is not permitted")


class MemoryInvalid(ValueError):
    def __init__(self):
        super().__init__("Invalid memory operation or unsupported memory data version")


def scrub_source(source: Source) -> Source:
    data = source.model_dump()
    data["ref"] = scrub(data["ref"])
    for key in ("contains", "symbol"):
        if data[key] is not None:
            data[key] = scrub(data[key])
    return Source.model_validate(data)


def source_inspection_view(source: Source) -> dict:
    """Bound redacted legacy metadata for display without changing mutation validation."""
    data = source.model_dump()
    truncated = False
    for key, maximum in (("ref", 1024), ("symbol", 200), ("contains", 1000)):
        if data[key] is not None:
            text = scrub(data[key])
            truncated = truncated or len(text) > maximum
            data[key] = text[:maximum]
    if truncated:
        data["metadata_truncated"] = True
    return data


def sanitize_memory(request: MemoryFeedback | MemoryCorrection | MemoryChange):
    try:
        data = request.model_dump()
        for key in ("question", "content", "note"):
            if key in data:
                data[key] = scrub(data[key])
        if isinstance(request, MemoryCorrection):
            data["sources"] = [scrub_source(source).model_dump() for source in request.sources]
        return type(request).model_validate(data)
    except ValueError:
        raise MemoryInvalid() from None


def derive_operation_mac_key(storage_key: bytes) -> bytes:
    """Separate operation verification from encryption using the existing private key."""
    if not isinstance(storage_key, bytes) or len(storage_key) < 32:
        raise MemoryInvalid()
    return hmac.new(storage_key, b"colony.memory.operation-key/v1", hashlib.sha256).digest()


def wrap_operation_digest(digest: str, *, key: bytes) -> str:
    """Key-wrap a canonical digest, including a legacy verifier, without its request."""
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
        or not isinstance(key, bytes)
        or len(key) < 32
    ):
        raise MemoryInvalid()
    return hmac.new(
        key, b"colony.memory.operation-request/v2\x00" + bytes.fromhex(digest), hashlib.sha256
    ).hexdigest()


def operation_hash(request, grant: Grant, kind: str, *, key: bytes) -> str:
    payload = {"kind": kind, "actor": grant.member_id, "request": request.model_dump()}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return wrap_operation_digest(digest, key=key)


def replacement_id(request: MemoryCorrection, grant: Grant) -> str:
    payload = {
        "version": "colony.memory-correction/1",
        "actor": grant.member_id,
        "namespace": request.namespace,
        "operation_id": request.operation_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
