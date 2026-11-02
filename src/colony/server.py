import base64
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from colony.memory import MemoryConflict, MemoryForbidden, MemoryInvalid, MemoryNotFound
from colony.models import (
    Ask,
    GitContext,
    Grant,
    Keyfile,
    MemoryChange,
    MemoryCorrection,
    MemoryFeedback,
    MemoryInspect,
    Trail,
)
from colony.security import verify
from colony.store import Store


def create_app(
    data: Path,
    repo: Path,
    issuer: Path,
    *,
    max_age: int = 604800,
    threshold: float = 0.60,
    repository_id: str | None = None,
) -> FastAPI:
    if not repo.is_dir() or not issuer.is_file():
        raise ValueError("Configure an existing repository and trusted issuer public key")
    if repository_id is not None:
        GitContext(repository_id=repository_id, state="unavailable")
    store = Store(data, repo, repository_id=repository_id)
    app = FastAPI(title="Colony", version="0.1.0")
    app.state.store = store

    async def memory_error(request: Request, exc: Exception):
        failures = {
            MemoryForbidden: (403, "Memory operation denied"),
            MemoryNotFound: (404, "Memory not found"),
            MemoryConflict: (409, "Memory operation conflict"),
            MemoryInvalid: (422, "Invalid memory request"),
        }
        status, detail = failures[type(exc)]
        return JSONResponse(status_code=status, content={"detail": detail})

    for error_type in (MemoryForbidden, MemoryNotFound, MemoryConflict, MemoryInvalid):
        app.add_exception_handler(error_type, memory_error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Pydantic errors can echo secret-bearing request bodies. Never send them back.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 200_000:
                return JSONResponse(status_code=413, content={"detail": "Request too large"})
        request._body = bytes(body)
        return await call_next(request)

    def member(authorization: str = Header(default="")) -> Grant:
        try:
            if not authorization.startswith("Bearer ") or len(authorization) > 32000:
                raise ValueError("Missing credential")
            keyfile = Keyfile.model_validate_json(
                base64.b64decode(authorization[7:], altchars=b"-_", validate=True)
            )
            grant = verify(keyfile, issuer)
            store.register(keyfile)
            return grant
        except (ValueError, TypeError):
            raise HTTPException(401, "Invalid or expired credential") from None

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/join")
    def join(grant: Annotated[Grant, Depends(member)]):
        return {"member_id": grant.member_id, "scopes": [s.model_dump() for s in grant.scopes]}

    @app.post("/capabilities")
    def capabilities(grant: Annotated[Grant, Depends(member)]):
        # Separate from /health, which retains its original public contract.
        from colony.context_budget import supported_encodings

        return {
            "git_context": {"version": "colony.git-context/1", "repository_id": repository_id},
            "context_budget": {
                "version": "colony.context-budget/1",
                "encodings": supported_encodings(),
                "min_tokens": 256,
                "max_tokens": 8192,
            },
        }

    @app.post("/trails")
    def push(trail: Trail, grant: Annotated[Grant, Depends(member)]):
        if not any(s.namespace == trail.namespace and s.push for s in grant.scopes):
            raise HTTPException(403, "Namespace push denied")
        if trail.capture == "outputs" and grant.capture != "outputs":
            raise HTTPException(403, "Output capture denied")
        try:
            return store.push(trail, grant)
        except ValueError:
            raise HTTPException(409, "Trail ID conflict") from None

    @app.post("/ask")
    def ask(query: Ask, grant: Annotated[Grant, Depends(member)]):
        namespaces = [s.namespace for s in grant.scopes if s.pull]
        if query.namespace is not None:
            if query.namespace not in namespaces:
                raise HTTPException(403, "Namespace pull denied")
            namespaces = [query.namespace]
        options = {}
        if query.context_budget is not None:
            from colony.context_budget import (
                ContextBudgetError,
                serialize_response,
                supported_encodings,
            )

            if query.context_budget.encoding not in supported_encodings():
                raise HTTPException(422, "Context budget encoding unavailable")
            options["context_budget"] = query.context_budget
        try:
            result = store.ask(
                query.question,
                namespaces,
                max_age,
                threshold,
                include_evidence_files=query.include_evidence_files,
                git_context=query.git_context,
                **options,
            )
        except ValueError as exc:
            if query.context_budget is not None and isinstance(exc, ContextBudgetError):
                raise HTTPException(422, "Context budget encoding unavailable") from None
            raise
        if query.context_budget is not None:
            return Response(content=serialize_response(result), media_type="application/json")
        return result

    @app.post("/memory/inspect")
    def memory_inspect(query: MemoryInspect, grant: Annotated[Grant, Depends(member)]):
        return store.inspect_memory(query.trail_id, query.namespace, grant)

    @app.post("/memory/feedback")
    def memory_feedback(query: MemoryFeedback, grant: Annotated[Grant, Depends(member)]):
        return store.feedback_memory(query, grant)

    @app.post("/memory/correct")
    def memory_correct(query: MemoryCorrection, grant: Annotated[Grant, Depends(member)]):
        return store.correct_memory(query, grant)

    @app.post("/memory/change")
    def memory_change(query: MemoryChange, grant: Annotated[Grant, Depends(member)]):
        return store.change_memory(query, grant)

    return app
