"""Bounded, read-only local Git provenance. Never publish Git errors or paths."""

import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from pathlib import Path

from colony.models import GitContext
from colony.security import scrub

_TOTAL_TIMEOUT = 3.0
_OUTPUT_LIMIT = 64 * 1024
_CLEANUP_RESERVE = 0.2
_IDENTIFIER = re.compile(r"[a-zA-Z0-9_.-]{1,80}")
_COMMIT = re.compile(rb"(?:[a-f0-9]{40}|[a-f0-9]{64})")


class _Unavailable(Exception):
    pass


class _Git:
    def __init__(self, repo: Path):
        self.repo = repo
        self.deadline = time.monotonic() + _TOTAL_TIMEOUT
        self.remaining_output = _OUTPUT_LIMIT
        self.executable = shutil.which("git")
        if self.executable is None:
            raise _Unavailable
        self.environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        self.environment.update(
            GIT_OPTIONAL_LOCKS="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_TERMINAL_PROMPT="0",
            LC_ALL="C",
            LANG="C",
        )
        self.options = [
            "--no-pager",
            "--no-optional-locks",
            "--no-lazy-fetch",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.preloadIndex=false",
        ]

    def run(self, *arguments: str) -> tuple[int, bytes, bytes]:
        """Drain bounded pipes concurrently; one deadline/output budget spans all calls."""
        run_deadline = self.deadline - _CLEANUP_RESERVE
        if time.monotonic() >= run_deadline:
            raise _Unavailable
        process = subprocess.Popen(
            [self.executable, *self.options, *arguments],
            cwd=self.repo,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = [bytearray(), bytearray()]
        lock = threading.Lock()
        failed = threading.Event()

        def drain(stream, index):
            try:
                with stream:
                    while chunk := stream.read(4096):
                        with lock:
                            accepted = min(len(chunk), self.remaining_output)
                            output[index].extend(chunk[:accepted])
                            self.remaining_output -= accepted
                            if accepted != len(chunk):
                                failed.set()
                                with suppress(OSError):
                                    process.kill()
                                break
            except Exception:
                failed.set()

        readers = [
            threading.Thread(target=drain, args=(stream, index), daemon=True)
            for index, stream in enumerate((process.stdout, process.stderr))
        ]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=max(0, run_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failed.set()
        finally:
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
                with suppress(OSError, subprocess.TimeoutExpired):
                    process.wait(timeout=max(0, self.deadline - time.monotonic()))
            for reader in readers:
                reader.join(timeout=max(0, self.deadline - time.monotonic()))
        if failed.is_set() or any(reader.is_alive() for reader in readers):
            raise _Unavailable
        if time.monotonic() >= run_deadline:
            raise _Unavailable
        return process.returncode, bytes(output[0]), bytes(output[1])

    def checked(self, *arguments: str) -> bytes:
        code, output, _ = self.run(*arguments)
        if code:
            raise _Unavailable
        return output

    def disable_filters(self) -> None:
        # Status can execute configured clean/process filters, even without a diff.
        # Enumerate KEY NAMES only, never values, across all effective system/global,
        # repository include files, local and worktree config. Keep normal config
        # semantics for built-in newline conversion and ignore rules.
        code, output, _ = self.run(
            "config",
            "--null",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|process|required)$",
        )
        if code not in (0, 1) or (code == 1 and output):
            raise _Unavailable
        prefixes = set()
        for raw in output.split(b"\0"):
            if not raw:
                continue
            key = raw.decode("utf-8")
            match = re.fullmatch(r"(filter\..+)\.(?:clean|process|required)", key)
            # Git's -c key=value syntax splits at the first '='. A valid
            # subsection containing it cannot be overridden safely this way.
            if (
                not match
                or "=" in match[1]
                or len(key) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in key)
            ):
                raise _Unavailable
            prefixes.add(match[1])
        if len(prefixes) > 32:
            raise _Unavailable
        for prefix in sorted(prefixes):
            self.options.extend(
                [
                    "-c",
                    prefix + ".clean=",
                    "-c",
                    prefix + ".process=",
                    "-c",
                    prefix + ".required=false",
                ]
            )

    def head(self) -> tuple[str, str | None]:
        commit = self.checked("rev-parse", "--verify", "HEAD^{commit}").strip()
        if not _COMMIT.fullmatch(commit):
            raise _Unavailable
        code, output, _ = self.run("symbolic-ref", "--quiet", "HEAD")
        if code == 1 and not output:
            branch = None
        elif code == 0 and output.startswith(b"refs/heads/"):
            branch = output.removesuffix(b"\n").removesuffix(b"\r")[len(b"refs/heads/") :]
            branch = branch.decode("utf-8")
            if not branch or scrub(branch) != branch:
                raise _Unavailable
        else:
            raise _Unavailable
        return commit.decode("ascii"), branch


def capture(repo: Path, repository_id: str) -> GitContext:
    """Collect local facts only; absent Git/unborn/bare/unknown state fails closed.

    Gitlinks are conservatively unavailable rather than recursively invoking Git in
    submodules. Custom clean filters are disabled, so filtered worktrees may be
    conservatively dirty. No index refresh, fsmonitor hook, fetch, or checkout runs.
    """
    if (
        not isinstance(repository_id, str)
        or not _IDENTIFIER.fullmatch(repository_id)
        or scrub(repository_id) != repository_id
    ):
        raise ValueError("A valid repository identifier is required.")
    unavailable = GitContext(repository_id=repository_id, state="unavailable")
    try:
        git = _Git(Path(repo))
        code, output, error = git.run("rev-parse", "--is-inside-work-tree")
        if code:
            if error == b"fatal: not a git repository (or any of the parent directories): .git\n":
                return GitContext(repository_id=repository_id, state="non_git")
            return unavailable
        if output.strip() != b"true":
            return unavailable
        git.disable_filters()
        initial = git.head()
        modes = git.checked("ls-files", "--format=%(objectmode)").splitlines()
        if any(mode not in (b"100644", b"100755", b"120000") for mode in modes):
            return unavailable
        dirty = bool(
            git.checked(
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
                "--ignore-submodules=all",
            )
        )
        if git.head() != initial:
            return unavailable
        commit, branch = initial
        return GitContext(
            repository_id=repository_id,
            state="git",
            commit=commit,
            branch=branch,
            detached=branch is None,
            dirty=dirty,
        )
    except Exception:
        return unavailable


def compare(saved: GitContext | None, requested: GitContext) -> str:
    """Compatibility only, never an authorization or freshness certificate."""
    if saved is None:
        return "legacy"
    if saved.repository_id != requested.repository_id:
        return "repository_mismatch"
    if saved.state == requested.state == "non_git":
        return "not_applicable"
    if saved.state != "git" or requested.state != "git":
        return "unavailable"
    if saved.dirty or requested.dirty:
        return "dirty"
    if saved.branch != requested.branch or saved.detached != requested.detached:
        return "branch_mismatch"
    if saved.commit != requested.commit:
        return "commit_mismatch"
    return "matched"
