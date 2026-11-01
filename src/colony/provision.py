"""Explicit, credential-free preparation of pinned local ONNX artifacts.

Manifest provenance: Hugging Face's /api/models/{repo}/tree/{revision} and
/api/models/{repo}/revision/{revision}?blobs=true (2026-10-31). Weight SHA256s
are the pinned LFS oids. Small-file SHA256s were calculated only after checking
their Git blob ids and sizes against that same authoritative pinned metadata.
No metadata, credentials, caches, or network are consulted by readiness checks.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

import httpx

# Each file is (exact byte length, trusted SHA256), independent of local caches.
_SHARED = {
    "tokenizer.json": (711396, "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
    "tokenizer_config.json": (
        1242,
        "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
    ),
    "vocab.txt": (231508, "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"),
}
MANIFEST = {
    "embed": {
        "repo": "qdrant/bge-small-en-v1.5-onnx-q",
        "revision": "aa8f8b060edb00e03bfdd08813a2949946c8ba55",
        "files": {
            **_SHARED,
            "config.json": (
                706,
                "13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd",
            ),
            "special_tokens_map.json": (
                695,
                "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
            ),
            "model_optimized.onnx": (
                66465124,
                "51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431",
            ),
        },
    },
    "rank": {
        "repo": "Xenova/ms-marco-MiniLM-L-6-v2",
        "revision": "a09144355adeed5f58c8ed011d209bf8ee5a1fec",
        "files": {
            **_SHARED,
            "config.json": (
                824,
                "d827779a72d27ae68cf878a6fc2e954542663fe21ca515d9f4783fc96be2d37e",
            ),
            "special_tokens_map.json": (
                125,
                "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
            ),
            "onnx/model.onnx": (
                90992115,
                "c623d0bcb99f4622beb413eaef00cfbe5db20df9f1dd982da4b4f26022881870",
            ),
        },
    },
}
_ROOT_MARKER = ".colony-models-v1"
_OWNER_MARKER = ".colony-model.json"
_COMPLETE_MARKER = ".colony-complete-v1"
_ROOT_BYTES = b"colony-models-v1\n"
_UNSAFE = "Model paths must be ordinary files and directories without links."


class ModelPreparationError(ValueError):
    """A controlled, fixed model-preparation message safe for CLI display.

    All raising sites in this module use fixed strings; unexpected transport, parser,
    or filesystem exceptions are normalized before they cross the public boundary.
    """


def _safe(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)
        ):
            raise ModelPreparationError(_UNSAFE)
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise ModelPreparationError(_UNSAFE)
    return path


def _valid(path: Path, expected: tuple[int, str]) -> bool:
    path = _safe(path)
    if not path.is_file() or path.stat().st_size != expected[0]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected[1]


def _ready(directory: Path, name: str) -> bool:
    return _safe(directory).is_dir() and all(
        _valid(directory / filename, expected)
        for filename, expected in MANIFEST[name]["files"].items()
    )


def check_models(directory: Path) -> dict:
    """Read bytes only; missing, partial, tampered, or linked models are not ready."""
    checks = []
    for name in MANIFEST:
        try:
            ready = _ready(Path(directory) / name, name)
        except (OSError, ValueError):
            ready = False
        checks.append(
            {
                "name": name,
                "ready": ready,
                "detail": "Pinned artifacts verified."
                if ready
                else "Pinned artifacts missing or invalid; run colony models fetch --dir MODEL_ROOT.",
            }
        )
    return {"ready": all(check["ready"] for check in checks), "models": checks}


def model_environment(directory: Path | None, *, offline: bool = False) -> dict[str, str]:
    """Return verified subprocess settings without changing this process's environment."""
    if directory is None and not offline:
        return {}
    result = {}
    for name in MANIFEST:
        variable = f"COLONY_{name.upper()}_MODEL_DIR"
        value = Path(directory) / name if directory is not None else os.environ.get(variable)
        try:
            if not value or not _ready(Path(value), name):
                raise ValueError("not ready")
            result[variable] = str(_safe(Path(value)))
        except (OSError, ValueError):
            raise ModelPreparationError(
                "Verified local models are required; run colony models fetch --dir MODEL_ROOT "
                "and pass --models MODEL_ROOT, or set both COLONY_EMBED_MODEL_DIR and "
                "COLONY_RANK_MODEL_DIR to verified snapshots."
            ) from None
    if offline:
        result.update(COLONY_OFFLINE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return result


def _owner(name: str) -> bytes:
    spec = MANIFEST[name]
    return json.dumps(
        {"version": 1, "repo": spec["repo"], "revision": spec["revision"]}, sort_keys=True
    ).encode()


def _matches(path: Path, expected: bytes) -> bool:
    path = _safe(path)
    return path.is_file() and path.stat().st_size == len(expected) and path.read_bytes() == expected


def _download(url: str, destination: Path, expected: tuple[int, str]) -> None:
    """Stream bounded bytes; never load implicit hub tokens, netrc, proxy credentials."""
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
        for _ in range(6):
            parsed = httpx.URL(url)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or not (
                    parsed.host == "huggingface.co"
                    or parsed.host.endswith(".huggingface.co")
                    or parsed.host.endswith(".hf.co")
                )
            ):
                raise ModelPreparationError("Untrusted model download destination.")
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                digest, size = hashlib.sha256(), 0
                with _safe(destination).open("xb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > expected[0]:
                            raise ModelPreparationError("Model artifact exceeds pinned size.")
                        digest.update(chunk)
                        output.write(chunk)
                if size != expected[0] or digest.hexdigest() != expected[1]:
                    raise ModelPreparationError("Model artifact failed pinned verification.")
                return
        raise ModelPreparationError("Model download redirect limit exceeded.")


def fetch_models(directory: Path) -> dict:
    """The only networked model-preparation operation; resume owned staging safely."""
    try:
        root = _safe(directory)
        if root.exists() and not root.is_dir():
            raise ModelPreparationError("Model root must be a dedicated directory.")
        marker = root / _ROOT_MARKER
        if root.exists() and any(root.iterdir()) and not _matches(marker, _ROOT_BYTES):
            raise ModelPreparationError(
                "Model root is not an owned Colony model directory; choose an empty root."
            )
        root.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            with _safe(marker).open("xb") as stream:
                stream.write(_ROOT_BYTES)
        for name, spec in MANIFEST.items():
            target, staging = root / name, root / f".{name}.partial"
            _safe(target)
            _safe(staging)
            if target.exists():
                if not _matches(target / _OWNER_MARKER, _owner(name)):
                    raise ModelPreparationError(
                        "Existing model directory is unowned; choose a new model root."
                    )
                if not _matches(target / _COMPLETE_MARKER, _owner(name)):
                    raise ModelPreparationError(
                        "Existing model directory is incomplete; choose a new model root."
                    )
                if not _ready(target, name):
                    raise ModelPreparationError(
                        "Completed model artifacts changed; choose a new model root."
                    )
                continue
            if staging.exists():
                if not staging.is_dir() or not _matches(staging / _OWNER_MARKER, _owner(name)):
                    raise ModelPreparationError(
                        "Partial model directory is unowned; choose a new model root."
                    )
            else:
                staging.mkdir()
                with (staging / _OWNER_MARKER).open("xb") as stream:
                    stream.write(_owner(name))
            # A partial directory may contain only the fixed manifest's files/subdirectories.
            allowed = {_OWNER_MARKER, _COMPLETE_MARKER}
            for filename in spec["files"]:
                allowed.update(
                    str(parent).replace("\\", "/")
                    for parent in [Path(filename), *Path(filename).parents]
                    if str(parent) != "."
                )
                allowed.add(filename + ".part")
            for path in staging.rglob("*"):
                _safe(path)
                if path.relative_to(staging).as_posix() not in allowed:
                    raise ModelPreparationError(
                        "Partial model directory has unexpected files; choose a new root."
                    )
            for filename, expected in spec["files"].items():
                path = _safe(staging / filename)
                if _valid(path, expected):
                    continue
                temporary = _safe(path.with_name(path.name + ".part"))
                path.parent.mkdir(parents=True, exist_ok=True)
                if temporary.exists():
                    if not temporary.is_file():
                        raise ModelPreparationError(_UNSAFE)
                    temporary.unlink()
                url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{filename}"
                _download(url, temporary, expected)
                if not _valid(temporary, expected):
                    raise ModelPreparationError("Model artifact failed pinned verification.")
                _safe(path)
                os.replace(temporary, path)
            if not _ready(staging, name):
                raise ModelPreparationError("Model artifact failed pinned verification.")
            completion = _safe(staging / _COMPLETE_MARKER)
            if completion.exists():
                if not _matches(completion, _owner(name)):
                    raise ModelPreparationError("Partial model completion marker is invalid.")
            else:
                with completion.open("xb") as stream:
                    stream.write(_owner(name))
            staging.rename(target)
        return check_models(root)
    except ModelPreparationError:
        raise
    except Exception:
        raise ModelPreparationError(
            "Model preparation failed; rerun models fetch to resume owned partial files."
        ) from None
