import ast
import hashlib
from pathlib import Path, PurePosixPath

from colony.models import Source

MAX_SOURCE_BYTES = 2_000_000


def repo_file(root: Path, ref: str) -> Path:
    # Resolve symlinks too; deny Windows paths even when the host is Linux.
    if "\\" in ref or ":" in ref or PurePosixPath(ref).is_absolute():
        raise ValueError("Source must be a repository-relative POSIX path")
    path = (root / ref).resolve()
    path.relative_to(root.resolve())
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source is missing or exceeds 2 MB")
    return path


def read_source(root: Path, ref: str) -> tuple[Path, bytes]:
    path = repo_file(root, ref)
    # The file may grow after the preliminary stat. Bound the actual read too;
    # never validate a truncated prefix or allocate an unbounded source here.
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Source is missing or exceeds 2 MB")
    return path, data


def snapshot(root: Path, ref: str) -> Source:
    _, data = read_source(root, ref)
    return Source(ref=ref, content_hash=hashlib.sha256(data).hexdigest())


def validate(root: Path, sources: list[Source]) -> tuple[str, list[dict]]:
    if not sources:
        return "unverified", []
    checks = []
    for source in sources:
        result = "unverified"
        reason = "Only repository file evidence is supported in the MVP"
        if source.kind == "file":
            try:
                path, data = read_source(root, source.ref)
                result = (
                    "validated"
                    if hashlib.sha256(data).hexdigest() == source.content_hash
                    else "stale"
                )
                reason = "File fingerprint matches" if result == "validated" else "File changed"
                if (
                    result == "validated"
                    and source.contains is not None
                    and source.contains not in data.decode("utf-8")
                ):
                    result, reason = "stale", "Expected configuration snippet is absent"
                if result == "validated" and source.symbol is not None:
                    if path.suffix != ".py":
                        result, reason = "unverified", "Symbol checks currently support Python only"
                    else:
                        tree = ast.parse(data)
                        names = {
                            n.name
                            for n in ast.walk(tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        }
                        if source.symbol not in names:
                            result, reason = "stale", "Expected Python symbol is absent"
            except (OSError, ValueError, SyntaxError) as exc:
                result, reason = "stale", f"Evidence unavailable ({type(exc).__name__})"
        checks.append({"ref": source.ref, "result": result, "reason": reason})
    status = (
        "stale"
        if any(c["result"] == "stale" for c in checks)
        else ("validated" if all(c["result"] == "validated" for c in checks) else "unverified")
    )
    return status, checks
