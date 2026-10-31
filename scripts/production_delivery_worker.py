"""Internal offline local worker. Fresh process, frozen application, disposable state."""

import ctypes
import json
import shutil
import socket
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from production_delivery_core import (
    RANK_QUESTION,
    check_budget,
    digest,
    file_manifest,
    fixture_tasks,
    operation_checks,
    put,
    read,
    require,
    sha,
    validate_plan,
    verify_environment,
    verify_loaded,
)


def peak_rss():
    """Process lifetime high-water RSS, not a per-operation allocation estimate."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in ["peak", "working", "qpeak", "q", "ppeak", "p", "page", "peakpage"]
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters.peak
        return None
    try:
        import resource

        size = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return size if sys.platform == "darwin" else size * 1024
    except (ImportError, OSError):
        return None


def run(plan, arm, source, fixtures, work, output):
    validate_plan(plan)
    actual_environment = verify_environment(plan["environment"])
    source, fixtures, work, output = map(Path, [source, fixtures, work, output])
    require(
        not any(n == "colony" or n.startswith("colony.") for n in sys.modules), "Preloaded Colony"
    )
    require(
        file_manifest(source) == plan["arms"][arm]["source_sha256"],
        "Exported implementation differs",
    )
    require(file_manifest(fixtures) == plan["fixture_sha256"], "Fixture bytes differ")
    require(fixture_tasks(fixtures) == plan["tasks"], "Fixture task contract differs")
    for name, expected in plan["models_sha256"].items():
        require(sha(Path(name)) == expected, "Pinned local model differs")

    def no_network(*args, **kwargs):
        raise RuntimeError("Offline local benchmark: network access forbidden")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    sys.path.insert(0, str(source / "src"))
    from colony.models import Grant, Scope, Trail
    from colony.retrieval import models, rank
    from colony.store import Store
    from colony.validation import snapshot

    started = time.monotonic()
    rows = []
    expected = plan["arms"][arm]["source_sha256"]
    verify_loaded(source, expected)

    def observe(case, operation, task=None):
        check_budget(output.parent, started, time.monotonic())
        tick, cpu = time.perf_counter(), time.process_time()
        result, error = None, None
        try:
            result = operation()
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        elapsed, cpu_elapsed = time.perf_counter() - tick, time.process_time() - cpu
        try:
            checked = (
                operation_checks(plan, case, result, fixtures)
                if error is None
                else {"operation_completed": False}
            )
        except Exception as exc:
            checked = {"oracle_completed": False}
            error = type(exc).__name__ + ": " + str(exc)
        row = {
            "arm": arm,
            "case_id": case,
            "revision": plan["arms"][arm]["revision"],
            "plan_sha256": plan["plan_sha256"],
            "question": task["question"]
            if task
            else RANK_QUESTION
            if case.startswith("rank-")
            else None,
            "namespace": ("other" if case.endswith("/scope") else task["namespace"])
            if task
            else None,
            "seconds": elapsed,
            "cpu_seconds": cpu_elapsed,
            "lifetime_peak_rss_bytes": peak_rss(),
            "checks": checked,
            "error": error,
            "result": result,
            "result_sha256": digest(result),
        }
        with (output / "observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        rows.append(row)
        check_budget(output.parent, started, time.monotonic())
        return result

    observe("model-load", lambda: (models(), {"models_loaded": True})[1])

    def run_task(index, task):
        prefix = task["id"] + "/"
        directory = work / task["id"]
        directory.mkdir()
        repo = directory / "repo"
        shutil.copytree(fixtures / "repos" / task["repository"], repo)
        state = directory / "state"
        store = None

        def initialize():
            nonlocal store
            store = Store(state, repo)
            return {"initialized": True}

        observe(prefix + "initialize", initialize, task)
        require(store is not None, "Store initialization failed; retain incomplete attempt")
        grant = Grant(
            member_id="producer",
            name="fixture producer",
            server="http://127.0.0.1:1",
            expires_at=4102444800,
            scopes=[Scope(namespace=task["namespace"], push=True, pull=True)],
        )

        def ask():
            return store.ask(task["question"], [task["namespace"]], 604800, 0.68)

        observe(prefix + "empty-miss", ask, task)
        trail = Trail(
            id=f"{index + 1:032x}",
            namespace=task["namespace"],
            question=task["question"],
            content=json.dumps({task["field"]: task["expected"]}),
            sources=[snapshot(repo, task["source"]), snapshot(repo, "README.md")],
            tainted=True,
        )
        observe(prefix + "push", lambda: store.push(trail, grant), task)

        for repeat in range(plan["repetitions"]):
            observe(prefix + f"hit-{repeat}", ask, task)
        observe(
            prefix + "scope", lambda: store.ask(task["question"], ["other"], 604800, 0.68), task
        )
        file = repo / task["source"]
        original = file.read_bytes()
        file.write_bytes(original + b"\n")
        observe(prefix + "stale", ask, task)
        file.write_bytes(original)

        def restart():
            nonlocal store
            store = Store(state, repo)
            return ask()

        observe(prefix + "restart", restart, task)

        def restore_backup():
            backup = directory / "restored"
            backup.mkdir()
            # SQLite backup API captures a consistent DB; retain encrypted payload/key together.
            with (
                closing(sqlite3.connect(state / "colony.db")) as original_db,
                closing(sqlite3.connect(backup / "colony.db")) as target,
            ):
                original_db.backup(target)
            shutil.copytree(state / "blobs", backup / "blobs")
            shutil.copyfile(state / "storage.key", backup / "storage.key")
            return Store(backup, repo).ask(task["question"], [task["namespace"]], 604800, 0.68)

        observe(prefix + "backup-restore", restore_backup, task)
        for clients in plan["clients"]:

            def simultaneous(n=clients):
                with ThreadPoolExecutor(max_workers=n) as pool:
                    return list(pool.map(lambda _: ask(), range(n)))

            observe(prefix + f"concurrent-{clients}", simultaneous, task)

    for index, task in enumerate(plan["tasks"]):
        run_task(index, task)
    for count, length in [(0, 0)] + [(n, size) for n in [1, 4, 12] for size in [64, 512, 4000]]:
        # Duplicates and mixed lengths expose ties/padding without asserting score equivalence.
        passages = [
            ((RANK_QUESTION + ". ") * (length // 24 + 1))[: max(1, length - i % 3)]
            for i in range(count)
        ]
        observe(
            f"rank-{count}-{length}",
            lambda p=passages: {"scores": rank(RANK_QUESTION, p), "passages": p},
        )
    for name, expected_hash in plan["models_sha256"].items():
        require(sha(Path(name)) == expected_hash, "Pinned local model changed during collection")
    proof = {
        "revision": plan["arms"][arm]["revision"],
        "source_sha256": file_manifest(source),
        "module_paths": verify_loaded(source, expected),
        "plan_sha256": plan["plan_sha256"],
        "observations": len(rows),
        "execution_environment": actual_environment,
    }
    put(output / "loaded-implementation.json", proof)


if __name__ == "__main__":
    plan_file, selected_arm, source_dir, fixture_dir, work_dir, output_dir = sys.argv[1:]
    run(read(plan_file), selected_arm, source_dir, fixture_dir, work_dir, output_dir)
