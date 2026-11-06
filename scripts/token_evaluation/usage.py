"""Prospective evaluator accounting; historical shared-reader behavior is unchanged."""

from colony.product_value import native_usage as _shared_native_usage


def native_usage(path):
    """Retain validated input/output subtotals even when a receipt is incomplete.

    These are known components, not a complete total. The shared reader's
    usage_complete/native_finished flags remain authoritative; missing fields,
    failed turns and malformed streams never become complete through this adapter.
    Cached input is already part of input, and auxiliary counters are not added.
    """
    parsed = _shared_native_usage(path)
    known = parsed["known_usage"]
    return {**parsed, "known_usage": {
        **known, "gross_tokens": known["input_tokens"] + known["output_tokens"],
    }}
