"""Frozen read argument/accounting contract, independent of the installed product."""
import json

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError


class _BatchEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(strict=True, min_length=1, max_length=1024)
    start: StrictInt = 1
    end: StrictInt | None = None
    offset: StrictInt = 0


class ReadArguments(BaseModel):
    path: str | None = None
    start: int = 1
    end: int | None = None
    offset: StrictInt = 0
    batch: list[_BatchEntry] | None = Field(default=None, min_length=1, max_length=8)


def raw_arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    if not isinstance(value, dict):
        return None
    value = value.copy()
    # Match FastMCP's top-level pre-parsing without coercing scalar strings.
    for name in ReadArguments.model_fields:
        if isinstance(value.get(name), str):
            try:
                decoded = json.loads(value[name])
            except RecursionError:
                return None
            except ValueError:
                continue
            if not isinstance(decoded, (str, int, float)):
                value[name] = decoded
    return value


def arguments(item):
    value = raw_arguments(item.get('arguments', {}))
    if value is None:
        return None
    try:
        args = ReadArguments.model_validate(value)
    except ValidationError:
        return None
    if args.batch is not None and (args.path is not None or args.start != 1
                                   or args.end is not None or args.offset != 0):
        return None
    return args


def read_units(value):
    """Charge every requested entry before validation; malformed/empty requests cost one."""
    # Invalid unrelated fields must not conceal requested batch entries.
    try:
        if isinstance(value, str):
            value = json.loads(value)
        batch = value.get('batch') if isinstance(value, dict) else None
        if isinstance(batch, str):
            batch = json.loads(batch)
    except (ValueError, RecursionError):
        return 1
    return max(1, len(batch)) if isinstance(batch, list) else 1


def split_reads(item, value):
    """Bind ordered results to ordered requests; never infer a path from returned text."""
    args = arguments(item)
    if args is None or not isinstance(value, dict):
        return []
    if args.batch is None:
        return [(item, value)]
    values = value.get('results')
    if not isinstance(values, list) or len(values) != len(args.batch):
        return []
    if any(not isinstance(v, dict) for v in values):
        return []
    return [({**item, 'arguments': request.model_dump()}, result)
            for request, result in zip(args.batch, values, strict=True)]
