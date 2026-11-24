"""Prospective evaluator accounting; historical shared-reader behavior is unchanged."""

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
    try:
        if request_metadata:
            diagnostics = analyze_bytes(source, optional_diagnostics=True)
            parsed = {**diagnostics["turn_accounting"], "request_usage": _request_usage(diagnostics)}
        else:
            try:
                parsed = _native_usage_bytes(source)
            except ValueError as exc:
                if str(exc) not in {"Unsupported native event format", "Report JSON nesting limit"}:
                    raise
                parsed = analyze_bytes(source, optional_diagnostics=True)["turn_accounting"]
    except (TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed native log") from exc
    known = parsed["known_usage"]
    return {**parsed, "known_usage": {
        **known, "gross_tokens": known["input_tokens"] + known["output_tokens"],
    }}


def _request_usage(diagnostics):
    response = diagnostics["response_usage"] or {}
    return {
        "status": "observed" if response.get("available") else "unavailable",
        "records": response.get("records") or None,
        "complete_records": response.get("complete_records"),
        "all_started_responses_completed": response.get("all_started_responses_completed"),
        "reconciles_with_turn_totals": response.get("reconciles_with_turn_totals"),
        "tool_groups": diagnostics["explicit_tool_response_groups"],
        "ungrouped_calls": diagnostics["tool_calls_without_response_binding"],
    }
