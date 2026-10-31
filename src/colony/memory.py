"""Shared online memory-operation contracts; error strings never include user content."""

import hashlib
import json

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


def operation_hash(request, grant: Grant, kind: str) -> str:
    payload = {"kind": kind, "actor": grant.member_id, "request": request.model_dump()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


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
