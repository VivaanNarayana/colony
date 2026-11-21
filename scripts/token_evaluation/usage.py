"""Prospective evaluator accounting; historical shared-reader behavior is unchanged."""

from contextlib import suppress

from colony.product_value import MAX_BYTES, _native_usage_bytes
from colony.report_io import bounded_bytes

if __package__ == "token_evaluation":  # Standalone scripts/token_evaluation.py entry point.
    from token_observability import analyze_bytes
else:
    from ..token_observability import analyze_bytes


def native_usage(path, *, request_metadata=False):
    """Retain validated input/output subtotals even when a receipt is incomplete.

    These are known components, not a complete total. The shared reader's
    usage_complete/native_finished flags remain authoritative; missing fields,
    failed turns and malformed streams never become complete through this adapter.
    Cached input is already part of input, and auxiliary counters are not added.
    """
    source = bounded_bytes(path, MAX_BYTES)
    diagnostics = None
    try:
        parsed = _native_usage_bytes(source)
    except ValueError as exc:
        if str(exc) != "Unsupported native event format":
            raise
        diagnostics = analyze_bytes(source)  # Accept only the known supplemental envelopes.
        parsed = diagnostics["turn_accounting"]
    except (TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed native log") from exc
    if request_metadata:
        if diagnostics is None:
            # Grouping may be unavailable on a valid turn stream, e.g. missing tool IDs.
            with suppress(ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError):
                diagnostics = analyze_bytes(source)
        parsed["request_usage"] = _request_usage(diagnostics)
    known = parsed["known_usage"]
    return {**parsed, "known_usage": {
        **known, "gross_tokens": known["input_tokens"] + known["output_tokens"],
    }}


def _request_usage(diagnostics):
    if diagnostics is None:
        return {"status": "unavailable", "records": None, "tool_groups": None}
    response = diagnostics["response_usage"]
    return {
        "status": "observed" if response["available"] else "unavailable",
        "records": response["records"] or None,
        "complete_records": response["complete_records"],
        "all_started_responses_completed": response["all_started_responses_completed"],
        "reconciles_with_turn_totals": response["reconciles_with_turn_totals"],
        "tool_groups": diagnostics["explicit_tool_response_groups"],
        "ungrouped_calls": diagnostics["tool_calls_without_response_binding"],
    }
