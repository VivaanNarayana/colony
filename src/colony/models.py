from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Model):
    namespace: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    push: bool = False
    pull: bool = False


class Grant(Model):
    member_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    server: str
    expires_at: int
    scopes: list[Scope] = Field(min_length=1, max_length=100)
    capture: Literal["none", "outputs"] = "outputs"


class Keyfile(Model):
    grant: Grant
    signature: str


class Source(Model):
    kind: Literal["file", "url", "doc"] = "file"
    ref: str = Field(min_length=1, max_length=1024)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str | None = Field(default=None, max_length=200)
    contains: str | None = Field(default=None, max_length=1000)


class Trail(Model):
    # Client-generated IDs make offline retries idempotent.
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    namespace: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    question: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=100000)
    sources: list[Source] = Field(default_factory=list, max_length=50)
    tainted: bool = True
    capture: Literal["none", "outputs"] = "none"


class Ask(Model):
    question: str = Field(min_length=1, max_length=2000)
    namespace: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    include_evidence_files: bool = False
