"""Descriptive reports from opt-in, local Colony client metadata only.

Dates mean UTC midnight. Timestamp filters require explicit UTC/offset and use
an inclusive lower bound and exclusive upper bound: [since, until).
"""

import math
import re
from collections import Counter
from datetime import datetime, timezone

from .usage import UsageJournal

CONTRACT = "colony.usage-report/1"
OPERATIONS = (
    "lookup",
    "publish",
    "retry",
    "inspect",
    "feedback",
    "correct",
    "retract",
    "restore",
    "change",
)
OUTCOMES = (
    "answer",
    "evidence",
    "miss",
    "stale",
    "unverified",
    "unavailable",
    "denied",
    "error",
    "queued",
    "accepted",
    "unknown",
    "not_found",
    "conflict",
    "invalid",
)
ASSESSMENTS = ("useful", "incorrect", "irrelevant")
LATENCY_SAMPLE_LIMIT = 10000
RECENT_LIMIT = 50


def parse_time(value):
    """Parse YYYY-MM-DD (UTC midnight), or ISO datetime with explicit UTC/offset."""
    if not isinstance(value, str):
        raise ValueError("Expected a date or ISO timestamp with explicit UTC/offset")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        # datetime accepts oversized offset minutes by normalizing them; reject them.
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("Invalid timestamp offset")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Use YYYY-MM-DD or ISO timestamp with explicit UTC/offset")
    return (parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()


def _bound(value):
    if value is None:
        return None
    if isinstance(value, str):
        return parse_time(value)
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("Invalid report time bound")
    return float(value)


def _ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "fraction": numerator / denominator if denominator else None,
    }


def _latency(events):
    # Retain an exact bounded sample, not an undocumented percentile estimator.
    selected = events[-LATENCY_SAMPLE_LIMIT:]
    values = sorted(e["duration_ms"] for e in selected)
    middle = len(values) // 2
    median = (
        (values[middle] if len(values) % 2 else values[middle - 1] / 2 + values[middle] / 2)
        if values
        else None
    )
    return {
        "measurement_scope": "instrumented_client_method_body_excluding_usage_journal_write",
        "observations": len(events),
        "samples": len(values),
        "sample_limit": LATENCY_SAMPLE_LIMIT,
        "selection": "most_recent_observed_attempts",
        "min_ms": values[0] if values else None,
        "median_ms": median,
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1] if len(values) >= 20 else None,
        "p95_minimum_samples": 20,
        "max_ms": values[-1] if values else None,
    }


def make_report(home, *, since=None, until=None):
    """Return a deterministic metadata-only report; no provider usage is inferred."""
    since, until = _bound(since), _bound(until)
    if since is not None and until is not None and since >= until:
        raise ValueError("Report since must precede until")
    journal = UsageJournal(home)
    status = journal.status()
    events = sorted(
        journal.events(since=since, until=until), key=lambda e: (e["timestamp"], e["event_id"])
    )
    by_operation = Counter(e["operation"] for e in events)
    by_outcome = Counter(e["outcome"] for e in events)
    lookups = [e for e in events if e["operation"] == "lookup"]
    lookup_outcomes = Counter(e["outcome"] for e in lookups)
    hits = [e for e in lookups if e["outcome"] in {"answer", "evidence"}]
    assessments = Counter(e.get("assessment") for e in hits)
    assessed = sum(assessments[label] for label in ASSESSMENTS)
    responses = sum(
        lookup_outcomes[k] for k in ("answer", "evidence", "miss", "stale", "unverified")
    )
    recent = lookups[-RECENT_LIMIT:]
    return {
        "contract": CONTRACT,
        "window": {
            "since": since,
            "until": until,
            "bounds": "since_inclusive_until_exclusive",
            "date_timezone": "UTC",
        },
        "coverage": {
            "scope": "local_instrumented_client_only",
            "enabled_now": status.get("enabled") is True,
            "health": status.get("health")
            if status.get("health") in {"healthy", "degraded", "unknown"}
            else "unknown",
            "gap": status.get("gap") is True,
            "warning": "Local observation gaps or journal issues may exist"
            if status.get("warning") or status.get("gap")
            else None,
            "retention_days": status.get("retention_days"),
            "max_retained_events": status.get("max_events"),
            "retained_events_all_time_windows": status.get("event_count"),
            "expired_events_removed": status.get("expired_count"),
            "capacity_events_removed": status.get("capacity_removed_count"),
            "first_observed_timestamp": events[0]["timestamp"] if events else None,
            "last_observed_timestamp": events[-1]["timestamp"] if events else None,
        },
        "activity": {
            "observed_request_attempts": len(events),
            "identified_logical_operations": len(
                {e["logical_id"] for e in events if e.get("logical_id") is not None}
            ),
            "attempts_without_logical_id": sum(e.get("logical_id") is None for e in events),
            "logical_operation_note": "Retries may share an ID; unidentified attempts cannot be deduplicated",
            "by_operation": {key: by_operation[key] for key in OPERATIONS},
            "by_outcome": {key: by_outcome[key] for key in OUTCOMES},
        },
        "lookups": {
            "attempts": len(lookups),
            "response_attempts": responses,
            "answer_hits": lookup_outcomes["answer"],
            "evidence_hits": lookup_outcomes["evidence"],
            "misses": lookup_outcomes["miss"],
            "stale": lookup_outcomes["stale"],
            "unverified": lookup_outcomes["unverified"],
            "unavailable": lookup_outcomes["unavailable"],
            "denied": lookup_outcomes["denied"],
            "errors": lookup_outcomes["error"],
            "other_outcomes": sum(
                lookup_outcomes[k]
                for k in OUTCOMES
                if k
                not in {
                    "answer",
                    "evidence",
                    "miss",
                    "stale",
                    "unverified",
                    "unavailable",
                    "denied",
                    "error",
                }
            ),
            "hit_fraction_of_responses": _ratio(len(hits), responses),
            "hit_fraction_of_attempts": _ratio(len(hits), len(lookups)),
        },
        "quality": {
            "source": "explicit_self_report_only",
            "eligible_answer_or_evidence_hits": len(hits),
            "assessed_hits": assessed,
            "unassessed_hits": len(hits) - assessed,
            "assessments": {label: assessments[label] for label in ASSESSMENTS},
            "assessment_coverage": _ratio(assessed, len(hits)),
            "self_reported_usefulness": _ratio(assessments["useful"], assessed),
            "self_reported_incorrect": _ratio(assessments["incorrect"], assessed),
            "correctness": "not_validated",
            "wrong_reuse": "not_independently_measured",
        },
        "latency": {"all_attempts": _latency(events), "lookup_attempts": _latency(lookups)},
        "model_usage": {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "token_savings": None,
            "dollar_savings": None,
            "reason": "Requires an explicitly imported compatible matched comparison",
        },
        "recent_lookup_metadata": [
            {
                key: e.get(key)
                for key in ("event_id", "timestamp", "outcome", "assessment", "duration_ms")
            }
            for e in reversed(recent)
        ],
        "recent_lookup_metadata_limit": RECENT_LIMIT,
        "limitations": [
            "Local retained observations only; disabled periods, other clients and evicted history are not measured",
            "Request attempts and logical operation IDs are not model tokens or saved work",
            "Answer/evidence hits and memory feedback do not establish correctness or usefulness",
            "Quality labels cover only explicitly assessed lookup hits and may be selectively reported",
            "Latency describes observed client attempts, including failures; p95 needs at least 20 samples",
            "Operation latency excludes the local usage journal write; it is not full CLI or workflow latency",
            "The report command does not collect provider/model usage or establish a product win",
        ],
    }


def render_text(report):
    """Render the versioned report with fixed labels and no local paths/content."""
    activity, lookup, quality = report["activity"], report["lookups"], report["quality"]
    coverage = report["coverage"]
    lines = [
        "Colony local usage report (colony.usage-report/1)",
        "Coverage: local instrumented client only; retained observations, not complete history.",
        f"Journal: {'enabled' if coverage['enabled_now'] else 'disabled'}; health: {coverage['health']}; "
        f"observation gap: {'yes' if coverage['gap'] else 'not established'}.",
        f"Window: since {report['window']['since']} inclusive; until {report['window']['until']} exclusive "
        "(UTC epoch seconds; None means unbounded).",
        f"Retention: {coverage['retention_days']} days; event cap: {coverage['max_retained_events']}; "
        f"expired events removed: {coverage['expired_events_removed']}; "
        f"capacity events removed: {coverage['capacity_events_removed']} (None means unknown).",
        f"Observed request attempts: {activity['observed_request_attempts']}; "
        f"identified logical operations: {activity['identified_logical_operations']}; "
        f"attempts without logical ID: {activity['attempts_without_logical_id']}.",
        f"Lookup attempts: {lookup['attempts']}; answer hits: {lookup['answer_hits']}; "
        f"evidence hits: {lookup['evidence_hits']}; misses: {lookup['misses']}; "
        f"stale: {lookup['stale']}; unverified: {lookup['unverified']}.",
        f"Lookup unavailable: {lookup['unavailable']}; denied: {lookup['denied']}; "
        f"errors: {lookup['errors']}; other: {lookup['other_outcomes']}.",
        f"Self-reported quality: {quality['assessed_hits']} assessed / "
        f"{quality['eligible_answer_or_evidence_hits']} eligible hits; "
        f"{quality['unassessed_hits']} unassessed. Correctness is not validated.",
        "Self-reported labels: "
        + "; ".join(f"{label}: {quality['assessments'][label]}" for label in ASSESSMENTS),
        "Model tokens, token savings and dollar savings: unknown; use an explicit matched comparison.",
    ]
    latency = report["latency"]["lookup_attempts"]
    median = "unknown" if latency["median_ms"] is None else f"{latency['median_ms']:.3f} ms"
    p95 = (
        "unknown (requires at least 20 samples)"
        if latency["p95_ms"] is None
        else f"{latency['p95_ms']:.3f} ms"
    )
    lines.append(
        f"Lookup operation latency (excludes usage journal write): {latency['samples']} samples; "
        f"median {median}; p95 {p95}."
    )
    lines.extend(report["limitations"])
    return "\n".join(lines) + "\n"
