"""Effective per-call deadline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp_serve_core.config import MAX_TIMEOUT_SECS

TOOL_TIMEOUT_MARGIN = 30
"""Seconds added to a tool's own `timeout_seconds` so the tool reports its timeout before the
server abandons it."""

TIMEOUT_ARGUMENT = "timeout_seconds"

_U64_MAX = 2**64 - 1


def schema_timeout_default(input_schema: Mapping[str, Any]) -> object:
    """The `properties.timeout_seconds.default` of a tool's input schema, or None."""
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    timeout = properties.get(TIMEOUT_ARGUMENT)
    if not isinstance(timeout, Mapping):
        return None
    return timeout.get("default")


def effective_timeout(configured: int, args: Mapping[str, Any], schema_default: object) -> int:
    """Deadline in whole seconds for one call.

    A tool that takes its own `timeout_seconds` (for example a long-poll wait tool) is allowed
    to run that long, from the call's argument or the schema default, plus a margin; the
    configured timeout is the floor. The argument counts only as a positive integer that fits an
    unsigned 64-bit value (booleans excluded); anything else falls back to `schema_default`,
    which counts as any such integer of zero or more, and then to `configured`.

    Args:
        configured: The configured deadline in seconds.
        args: The call arguments.
        schema_default: The schema's `properties.timeout_seconds.default`, None when absent.
    """
    requested = args.get(TIMEOUT_ARGUMENT)
    if type(requested) is int and 0 < requested <= _U64_MAX:
        return _with_margin(configured, requested)
    if type(schema_default) is int and 0 <= schema_default <= _U64_MAX:
        return _with_margin(configured, schema_default)
    return configured


def _with_margin(configured: int, requested: int) -> int:
    return max(configured, min(requested, MAX_TIMEOUT_SECS) + TOOL_TIMEOUT_MARGIN)
