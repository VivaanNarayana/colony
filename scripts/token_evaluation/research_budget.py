"""Bounded procedural budget receipts, never source evidence or token discounts."""
import json


def receipt(block):
    if not isinstance(block, dict) or block.get('type') != 'text':
        return None
    text = block.get('text')
    if not isinstance(text, str) or len(text) > 1024:
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or set(value) not in (
            {'research_budget'}, {'error', 'research_budget'}):
        return None
    budget = value['research_budget']
    if not isinstance(budget, dict) or set(budget) != {'admitted', 'denied', 'succeeded', 'remaining'}:
        return None
    if not all(type(v) is int and 0 <= v <= 1_000_000 for v in budget.values()):
        return None
    if not (budget['succeeded'] <= budget['admitted'] <= 12 and budget['remaining'] <= 12
            and budget['admitted'] + budget['remaining'] <= 12):
        return None
    return budget


def source_blocks(blocks):
    # Only standalone successful telemetry blocks are removed from outcome parsing.
    # Keep error envelopes so the normal sanitizer can classify limit denials.
    return [b for b in blocks if receipt(b) is None or '"error"' in b['text']]
