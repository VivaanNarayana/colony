"""Shared prospective answer instructions and provider-free structural checks."""
from __future__ import annotations

import json

OUTPUT_CONTRACT = "colony.token-evaluation-output/1"
JSON_INSTRUCTION = (
    "Final answer must be exactly one JSON object with top-level keys \"facts\" and "
    "\"sources\". Both keys are required at the top level; sources nested inside facts "
    "does not satisfy this requirement. Do not wrap the object in Markdown fences or "
    "add surrounding prose. Progress is evaluated separately under the frozen "
    "extraction policy."
)


def validate_output(output):
    if not isinstance(output, dict) or output.get("contract") != OUTPUT_CONTRACT:
        raise ValueError("Missing or unsupported frozen output contract")
    if output.get("format") not in {"json", "free_text"}:
        raise ValueError("Freeze output contract format")
    if output.get("answer_policy") not in {"last_agent_message", "all_agent_messages"}:
        raise ValueError("Freeze answer extraction")
    return output


def instruction(output):
    validate_output(output)
    return JSON_INSTRUCTION if output["format"] == "json" else ""


def _invalid_constant(value):
    raise ValueError("Nonfinite values are not JSON")


def structurally_compliant(text, output):
    """Check JSON syntax and required keys only; independent quality is still required."""
    validate_output(output)
    if output["format"] == "free_text":
        return True
    if not isinstance(text, str):
        return False
    try:
        # Only structure matters: do not impose Python's integer-size/float limits
        # on otherwise valid JSON values that remain for independent quality review.
        value = json.loads(text, parse_constant=_invalid_constant, parse_int=str, parse_float=str)
    except (TypeError, ValueError, RecursionError):
        return False
    return isinstance(value, dict) and {"facts", "sources"} <= value.keys()
