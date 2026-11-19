"""Prepare the pinned PR15 evaluator overlay; never dispatch native/model sessions."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

BASE = "1ee4cd787b9cf65d8df82cbcf9142ad0208208e7"
ROOT = Path(__file__).resolve().parents[1]
OVERLAY = Path(__file__).with_name("pr15_evaluation_overlay")
PREFIXES = ("scripts/token_evaluation/", "tests/token_evaluations/")
FILES = {"scripts/token_evaluation.py", "docs/token-evaluations.md"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=True, timeout=60
    ).stdout


def checked_name(name):
    path = PurePosixPath(name)
    if (
        path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
        or not (name in FILES or name.startswith(PREFIXES))
    ):
        raise ValueError("Unexpected evaluator path")
    return name


def prepare(output, *, repo=ROOT):
    """Export exact Git blobs and apply only the reviewed, hash-bound patch."""
    requested = Path(output).absolute()
    if os.name == "nt" and requested.drive.lower() != "d:":
        raise ValueError("Generated evaluator files must use D storage")
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
           for p in (requested, *requested.parents)):
        raise ValueError("Linked output path rejected")
    output = requested.resolve()
    if output.exists() or output.is_relative_to(Path(repo).resolve()):
        raise ValueError("Output must be new and outside the repository")
    manifest = json.loads((OVERLAY / "manifest.json").read_text(encoding="utf-8"))
    patch = (OVERLAY / "correction.patch").read_bytes()
    if (manifest["base"] != BASE or manifest["patch_sha256"] != sha(patch)
            or manifest["contract"] != "colony.pr15-evaluator-overlay/1"):
        raise ValueError("Overlay manifest/patch mismatch")
    names = git(repo, "ls-tree", "-r", "--name-only", BASE, "--",
                "scripts/token_evaluation.py", "scripts/token_evaluation",
                "tests/token_evaluations", "docs/token-evaluations.md").decode().splitlines()
    if set(names) != set(manifest["base_files"]):
        raise ValueError("Pinned evaluator inventory mismatch")
    blobs = {}
    for name in names:
        checked_name(name)
        mode = git(repo, "ls-tree", BASE, "--", name).decode().split()[0]
        if mode != "100644":
            raise ValueError("Only regular evaluator blobs may be exported")
        data = git(repo, "cat-file", "blob", BASE + ":" + name)
        if sha(data) != manifest["base_files"][name]:
            raise ValueError("Pinned evaluator blob mismatch")
        blobs[name] = data
    for name in manifest["patched_files"]:
        checked_name(name)
    # Validate patch targets before allowing Git to write into the disposable export.
    import re

    targets = re.findall(rb"^\+\+\+ b/(.+)$", patch, re.MULTILINE)
    originals = re.findall(rb"^--- a/(.+)$", patch, re.MULTILINE)
    if (not targets or targets != originals or len(set(targets)) != len(targets)
            or {n.decode() for n in targets} != set(manifest["patched_files"])):
        raise ValueError("Unexpected patch targets")
    output.mkdir(parents=True)
    for name, data in blobs.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    for flag in ("--check", None):
        command = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                   "apply", "--whitespace=error"]
        if flag:
            command.append(flag)
        subprocess.run(command, input=patch, cwd=output, capture_output=True,
                       check=True, timeout=30)
    actual = {p.relative_to(output).as_posix(): sha(p.read_bytes())
              for p in output.rglob("*") if p.is_file()}
    expected = dict(manifest["base_files"], **manifest["patched_files"])
    if actual != expected:
        raise ValueError("Patched evaluator inventory mismatch")
    receipt = {
        "contract": manifest["contract"], "base": BASE, "patch_sha256": sha(patch),
        "files": actual, "native_sessions_started": 0, "native_evidence": False,
        "limitations": ["Offline controls are not measured token savings.",
                        "New native runs need a new frozen protocol and owner authorization.",
                        "Historical protocols, labels and raw evidence are unchanged."],
    }
    (output / "overlay-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Evaluator preparation failed: {exc}\n")
    print(json.dumps({"output": str(args.output), "base": receipt["base"],
                      "patch_sha256": receipt["patch_sha256"], "native_sessions_started": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
