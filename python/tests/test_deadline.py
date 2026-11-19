from typing import Any

import pytest

from mcp_serve_core import (
    MAX_TIMEOUT_SECS,
    TOOL_TIMEOUT_MARGIN,
    effective_timeout,
    schema_timeout_default,
)

CONFIGURED = 120


@pytest.mark.parametrize(
    ("name", "args", "schema_default", "want"),
    [
        ("no argument and no default", {}, None, CONFIGURED),
        ("argument clears the tool's own wait plus margin", {"timeout_seconds": 1800}, 300, 1830),
        ("schema default used when the argument is absent", {}, 300, 330),
        ("configured is the floor", {"timeout_seconds": 5}, None, CONFIGURED),
        ("a schema default below the floor", {}, 10, CONFIGURED),
        (
            "an absurd argument is clamped",
            {"timeout_seconds": 2**64 - 1},
            None,
            MAX_TIMEOUT_SECS + TOOL_TIMEOUT_MARGIN,
        ),
        (
            "an argument past u64 falls back to the schema default",
            {"timeout_seconds": 2**64},
            300,
            330,
        ),
        (
            "an argument past u64 with no default is the configured floor",
            {"timeout_seconds": 2**64},
            None,
            CONFIGURED,
        ),
        ("a schema default past u64 is the configured floor", {}, 2**64, CONFIGURED),
        ("zero argument falls back to the schema default", {"timeout_seconds": 0}, 300, 330),
        ("negative argument falls back to the schema default", {"timeout_seconds": -5}, 300, 330),
        ("boolean argument falls back to the schema default", {"timeout_seconds": True}, 300, 330),
        (
            "fractional argument falls back to the schema default",
            {"timeout_seconds": 10.5},
            300,
            330,
        ),
        (
            "whole float argument falls back to the schema default",
            {"timeout_seconds": 60.0},
            300,
            330,
        ),
        ("string argument falls back to the schema default", {"timeout_seconds": "1800"}, 300, 330),
        ("null argument falls back to the schema default", {"timeout_seconds": None}, 300, 330),
        ("unusable argument and no default", {"timeout_seconds": "soon"}, None, CONFIGURED),
        ("boolean schema default is ignored", {}, True, CONFIGURED),
        ("string schema default is ignored", {}, "300", CONFIGURED),
        ("float schema default is ignored", {}, 300.0, CONFIGURED),
        ("negative schema default is ignored", {}, -1, CONFIGURED),
    ],
)
def test_effective_timeout(
    name: str, args: dict[str, Any], schema_default: object, want: int
) -> None:
    assert effective_timeout(CONFIGURED, args, schema_default) == want


def test_a_schema_default_of_zero_is_not_filtered() -> None:
    assert effective_timeout(10, {}, 0) == TOOL_TIMEOUT_MARGIN
    assert effective_timeout(CONFIGURED, {}, 0) == CONFIGURED


def test_a_zero_argument_does_not_mask_a_zero_default() -> None:
    assert effective_timeout(10, {"timeout_seconds": 0}, 0) == TOOL_TIMEOUT_MARGIN


def test_the_argument_cap_applies_before_the_margin() -> None:
    got = effective_timeout(CONFIGURED, {"timeout_seconds": MAX_TIMEOUT_SECS + 1}, None)
    assert got == MAX_TIMEOUT_SECS + TOOL_TIMEOUT_MARGIN


@pytest.mark.parametrize(
    ("schema", "want"),
    [
        ({"properties": {"timeout_seconds": {"type": "integer", "default": 300}}}, 300),
        ({"properties": {"timeout_seconds": {"type": "integer"}}}, None),
        ({"properties": {}}, None),
        ({"properties": "nope"}, None),
        ({"properties": {"timeout_seconds": 5}}, None),
        ({}, None),
    ],
)
def test_schema_timeout_default(schema: dict[str, Any], want: object) -> None:
    assert schema_timeout_default(schema) == want
