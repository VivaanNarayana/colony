import base64
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from colony.models import Ask, Grant, Keyfile, Trail
from colony.security import verify
from colony.store import Store


def create_app(
    data: Path, repo: Path, issuer: Path, *, max_age: int = 604800, threshold: float = 0.62
) -> FastAPI:
    if not repo.is_dir() or not issuer.is_file():
        raise ValueError("Configure an existing repository and trusted issuer public key")
    store = Store(data, repo)
    app = FastAPI(title="Colony", version="0.1.0")
    app.state.store = store

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
        return store.ask(query.question, namespaces, max_age, threshold)

    return app
