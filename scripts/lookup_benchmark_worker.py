"""Private JSONL worker: every Colony module comes from one frozen revision."""

import argparse
import hashlib
import importlib
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    for name in ["store", "context", "retrieval", "models", "security", "validation"]:
        importlib.import_module("colony." + name)
    paths = {
        name: str(Path(module.__file__).resolve())
        for name, module in sys.modules.items()
        if (name == "colony" or name.startswith("colony.")) and getattr(module, "__file__", None)
    }
    if not all(Path(p).is_relative_to(source) for p in paths.values()):
        raise RuntimeError("Colony import escaped frozen implementation")
    proof = {
        "source": str(source),
        "module_paths": paths,
        "source_sha256": {
            p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*.py")
        },
    }
    print(json.dumps({"proof": proof}), flush=True)
    if args.probe_only:
        return

    from colony import context
    from colony import store as module
    from colony.models import Grant, Scope, Trail
    from colony.validation import snapshot

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    stores, cases = {}, {c["task_id"]: c for c in spec["cases"]}
    grant = Grant(
        member_id="benchmark",
        name="Benchmark",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 86400,
        scopes=[Scope(namespace=n, push=True, pull=True) for n in ["ops", "finance"]],
    )
    for i, case in enumerate(cases.values()):
        root = args.root / case["task_id"]
        repo = root / "repo"
        repo.mkdir(parents=True)
        for ref, text in case["files"].items():
            path = repo / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        store = module.Store(root / "data", repo)
        for j, trail in enumerate(case["trails"]):
            identifier = f"{i * 100 + j + 1:032x}"
            store.push(
                Trail(
                    id=identifier,
                    namespace=trail["namespace"],
                    question=trail["question"],
                    content=trail["content"],
                    sources=[snapshot(repo, ref) for ref in trail["refs"]],
                ),
                grant,
            )
            with store.connect() as db:
                db.execute(
                    "UPDATE trails SET created_at=? WHERE id=?",
                    (time.time() - trail.get("age_seconds", 0), identifier),
                )
        # Warm model/session caches before changing sources, outside measured asks.
        store.ask(case["question"], case["namespaces"], 3600, 0.60)
        for ref, text in case.get("mutations", {}).items():
            path = repo / ref
            if text is None:
                path.unlink()
            else:
                path.write_bytes(text.encode("utf-8"))
        stores[case["task_id"]] = store

    counts = {"query_embeddings": 0, "reranker_calls": 0, "reranked_passages": 0}
    original_embed = module.embed

    def embed(text, *, query=False):
        counts["query_embeddings"] += int(query)
        return original_embed(text, query=query)

    module.embed = embed
    for owner in [module, context]:
        original = owner.rank

        def rank(question, passages, _original=original):
            counts["reranker_calls"] += bool(passages)
            counts["reranked_passages"] += len(passages)
            return _original(question, passages)

        owner.rank = rank
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if command.get("stop"):
            return
        task = command["task_id"]
        case = cases[task]
        before = dict(counts)
        started = time.perf_counter()
        result = stores[task].ask(case["question"], case["namespaces"], 3600, 0.60)
        seconds = time.perf_counter() - started
        print(
            json.dumps(
                {"result": result, "seconds": seconds, **{k: counts[k] - before[k] for k in counts}}
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
