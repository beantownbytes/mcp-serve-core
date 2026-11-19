import json
from pathlib import Path
from typing import Any

import pytest

from mcp_serve_core import (
    CallOutcome,
    Canceled,
    Config,
    Panicked,
    Policy,
    Success,
    TimedOut,
    Tool,
    UnknownTool,
    into_result,
)
from mcp_serve_core.testing import result_text

FIXTURE = Path(__file__).resolve().parents[2] / "testdata" / "golden_results.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _outcome(name: str, args: Any) -> CallOutcome:
    match name:
        case "UnknownTool":
            return UnknownTool(args)
        case "TimedOut":
            return TimedOut(args)
        case "Canceled":
            return Canceled()
        case "Panicked":
            return Panicked()
        case "Success":
            return Success(args)
        case "Policy":
            return Policy(args)
        case "Tool":
            return Tool(args)
        case "Config":
            return Config(args)
    raise AssertionError(f"unknown outcome {name!r} in the golden fixture")


def test_the_fixture_covers_every_outcome() -> None:
    covered = {case["outcome"] for case in CASES}
    assert covered == {
        "UnknownTool",
        "TimedOut",
        "Canceled",
        "Panicked",
        "Success",
        "Policy",
        "Tool",
        "Config",
    }


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_golden_result(case: dict[str, Any]) -> None:
    result = into_result(_outcome(case["outcome"], case["args"]), case["cap"])
    text = result_text(result)
    assert text == case["expect"]["text"]
    assert result.is_error is case["expect"]["isError"]
