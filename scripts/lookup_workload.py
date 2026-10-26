"""Versioned API-contract fixtures and an oracle independent of Colony internals.

These are public development cases, not held-out evidence. Expectations follow
documented retrieval, source, scope and freshness behavior; no model scores or
eligibility thresholds are mocked or encoded here.
"""

import copy
import hashlib
import json
import random

VERSION = 1
MAX_AGE = 3600


def workload(seed=20261021, variants=3):
    rng = random.Random(seed)
    cases = []
    for variant in range(variants):
        days = rng.randrange(12, 97)
        codec = rng.choice(["zstd", "lz4", "gzip"])
        fact = f"Archive retention interval is {days} days."
        command = f"archivectl sweep --dry-run --max-age {days}"
        question = "What is the archive retention interval?"
        text = (
            f"# Archive operations\n\n## Retention\n\n{fact}\n\n"
            f"## Backup transport\n\nBackup transport codec is {codec}.\n\n"
            f"## Sweep preview\n\nRun `{command}` to preview the archive sweep.\n"
        )
        trail = {"question": question, "content": fact, "namespace": "ops", "refs": ["ops.md"]}
        base = {
            "files": {"ops.md": text},
            "trails": [trail],
            "question": question,
            "namespaces": ["ops"],
            "expected": {"hit": True, "status": "validated", "evidence_contains": [fact]},
        }

        def add(name, _base=base, _variant=variant, **changes):
            case = copy.deepcopy(_base)
            case.update(changes)
            case["task_id"] = f"{name}-{_variant}"
            case["family"] = name
            cases.append(case)

        add("exact-fact")
        add("paraphrase", question="How long do archived records stay available?")
        add(
            "source-only-fact",
            question="What codec is used for backup transport?",
            expected={
                "hit": True,
                "status": "validated",
                "evidence_contains": [f"codec is {codec}"],
            },
        )
        add(
            "exact-command",
            question="How do I preview the archive sweep?",
            trails=[
                {**trail, "question": "How do I preview the archive sweep?", "content": command}
            ],
            expected={"hit": True, "status": "validated", "evidence_contains": [command]},
        )
        add("empty-memory", trails=[], expected={"hit": False})
        add(
            "unrelated",
            question="What are the microscope calibration settings?",
            expected={"hit": False},
        )
        canary = f"PRIVATE_CANARY_{rng.getrandbits(64):016x}"
        secret = {**trail, "namespace": "finance", "content": canary, "refs": ["private.md"]}
        add(
            "namespace-denied",
            files={"private.md": canary},
            trails=[secret],
            expected={"hit": False, "forbidden": [canary, "finance"]},
        )
        add("no-scopes", namespaces=[], expected={"hit": False, "forbidden": [fact]})
        for name, replacement in [
            ("changed-source", text.replace(fact, "Retention was removed.")),
            ("deleted-source", None),
        ]:
            add(name, mutations={"ops.md": replacement}, expected={"hit": True, "status": "stale"})
        add(
            "expired",
            trails=[{**trail, "age_seconds": MAX_AGE + 60}],
            expected={"hit": True, "status": "unverified", "age_flag": True},
        )
        add(
            "no-source",
            trails=[{**trail, "refs": []}],
            expected={"hit": True, "status": "unverified"},
        )
        overview = "The archive library supports restoration and backup management."
        add(
            "overview-is-not-procedure",
            files={"ops.md": overview},
            trails=[
                {**trail, "question": "What does the archive library support?", "content": overview}
            ],
            question="How do I restore the archive?",
            expected={"hit": False},
        )
    rng.shuffle(cases)
    return {"schema_version": VERSION, "seed": seed, "variants": variants, "cases": cases}


def assess(case, packet, repo):
    """Return failures, without consulting baseline output or implementation helpers."""
    try:
        return _assess(case, packet, repo)
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as error:
        return [f"Malformed packet/source: {type(error).__name__}"]


def _assess(case, packet, repo):
    errors = []
    if type(packet.get("hit")) is not bool:
        errors.append("hit must be a boolean")
    expected = case["expected"]
    for key in ["hit", "status", "age_flag"]:
        if key in expected and packet.get(key) != expected[key]:
            errors.append(f"{key}: expected {expected[key]!r}, got {packet.get(key)!r}")
    rendered = json.dumps(packet, ensure_ascii=False)
    for forbidden in expected.get("forbidden", []):
        if forbidden in rendered:
            errors.append("scoped data leaked into response")
    if packet.get("hit") is True and packet.get("namespace") not in case["namespaces"]:
        errors.append("hit namespace is outside requested scopes")
    evidence = packet.get("evidence", [])
    if packet.get("status") != "validated" and evidence:
        errors.append("nonvalidated result exposed current evidence")
    text = "\n".join(e.get("text", "") for e in evidence)
    for fact in expected.get("evidence_contains", []):
        if fact not in text:
            errors.append(f"required source fact absent: {fact}")
    if packet.get("status") == "validated":
        if not packet.get("hit") or not evidence or not packet.get("sources"):
            errors.append("validated hit must include current source evidence")
        hashes = {}
        for source in packet.get("sources", []):
            path = (repo / source["ref"]).resolve()
            if not path.is_relative_to(repo.resolve()) or not path.is_file():
                errors.append("source path is missing or escapes repository")
                continue
            if source["content_hash"] != hashlib.sha256(path.read_bytes()).hexdigest():
                errors.append("source fingerprint does not match current bytes")
            hashes[source["ref"]] = source["content_hash"]
        for e in evidence:
            path = (repo / e["ref"]).resolve()
            if not path.is_relative_to(repo.resolve()) or not path.is_file():
                errors.append("evidence path is missing or escapes repository")
                continue
            data = path.read_bytes()
            if hashes.get(e["ref"]) != hashlib.sha256(data).hexdigest():
                errors.append("evidence fingerprint does not match current bytes")
            lines = data.decode("utf-8").splitlines()
            line = e.get("line")
            if (
                type(line) is not int
                or line < 1
                or not "\n".join(lines[line - 1 :]).startswith(e["text"])
            ):
                errors.append("evidence text is not at the claimed source line")
    return errors
