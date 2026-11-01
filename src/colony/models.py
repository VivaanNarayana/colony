import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_serializer,
    model_validator,
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def _legacy_git_payload(self, handler):
        data = handler(self)
        # Absent provenance must preserve legacy payload hashes and queued retries.
        if data.get("git_context", False) is None:
            data.pop("git_context", None)
        if data.get("context_budget", False) is None:
            data.pop("context_budget", None)
        return data


class ContextBudget(Model):
    """Explicit response accounting; this does not identify an agent's billing model."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["colony.context-budget/1"] = "colony.context-budget/1"
    max_tokens: StrictInt = Field(default=2048, ge=256, le=8192)
    encoding: Literal["utf8_bytes", "o200k_base"] = "utf8_bytes"


class GitContext(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["colony.git-context/1"] = "colony.git-context/1"
    repository_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    state: Literal["git", "non_git", "unavailable"]
    commit: str | None = Field(default=None, pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    detached: StrictBool | None = None
    dirty: StrictBool | None = None

    @model_validator(mode="after")
    def consistent(self):
        from colony.security import scrub

        if scrub(self.repository_id) != self.repository_id or (
            self.branch is not None and scrub(self.branch) != self.branch
        ):
            raise ValueError("Git metadata must not contain credential-shaped values")
        if self.branch is not None and (
            re.search(r"[\x00-\x20\x7f~^:?*\[\\]", self.branch)
            or self.branch.startswith("/")
            or self.branch.endswith(("/", ".", ".lock"))
            or any(part.startswith(".") for part in self.branch.split("/"))
            or ".." in self.branch
            or "@{" in self.branch
            or "//" in self.branch
        ):
            raise ValueError("Invalid Git branch")
        if self.state == "git":
            if self.commit is None or self.detached is None or self.dirty is None:
                raise ValueError("Incomplete Git context")
            if self.detached != (self.branch is None):
                raise ValueError("Inconsistent Git branch")
        elif any(x is not None for x in (self.commit, self.branch, self.detached, self.dirty)):
            raise ValueError("Unavailable Git context must not invent repository state")
        return self


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
    git_context: GitContext | None = None


class Ask(Model):
    question: str = Field(min_length=1, max_length=2000)
    namespace: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    include_evidence_files: bool = False
    git_context: GitContext | None = None
    context_budget: ContextBudget | None = None


class MemoryInspect(Model):
    namespace: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    trail_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MemoryFeedback(MemoryInspect):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    verdict: Literal["helpful", "incorrect", "outdated"]
    note: str = Field(default="", max_length=1000)


class MemoryCorrection(MemoryInspect):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: int = Field(ge=0)
    question: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=4000)
    sources: list[Source] = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=1000)
    git_context: GitContext | None = None


class MemoryChange(MemoryInspect):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_revision: int = Field(ge=0)
    action: Literal["retract", "restore"]
    note: str = Field(default="", max_length=1000)
