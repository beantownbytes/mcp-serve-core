import pytest
from mcp_types import ToolAnnotations

from mcp_serve_core import (
    Access,
    ConfigError,
    PolicyError,
    ToolError,
    ToolFailure,
    ToolSpec,
)


def _spec(access: Access | None = None) -> ToolSpec:
    return ToolSpec("t", "d", {"type": "object"}, lambda _args: "", access)


def _hints(access: Access | None) -> ToolAnnotations | None:
    return _spec(access).to_tool().annotations


def test_read_only_emits_only_the_read_only_hint() -> None:
    hints = _hints(Access.READ_ONLY)
    assert hints is not None
    assert (hints.read_only_hint, hints.destructive_hint) == (True, None)


def test_write_is_not_read_only_and_not_destructive() -> None:
    hints = _hints(Access.WRITE)
    assert hints is not None
    assert (hints.read_only_hint, hints.destructive_hint) == (False, False)


def test_destructive_write_is_not_read_only_and_destructive() -> None:
    hints = _hints(Access.DESTRUCTIVE_WRITE)
    assert hints is not None
    assert (hints.read_only_hint, hints.destructive_hint) == (False, True)


def test_unclassified_emits_no_annotations() -> None:
    assert _hints(None) is None


def test_descriptor_carries_name_description_and_schema() -> None:
    tool = _spec().to_tool()
    assert tool.name == "t"
    assert tool.description == "d"
    assert tool.input_schema == {"type": "object"}


async def _async_handler(_args: dict[str, object]) -> str:
    return "ok"


def test_a_spec_accepts_a_sync_or_an_async_handler() -> None:
    assert ToolSpec("a", "d", {}, _async_handler).access is None
    assert ToolSpec("b", "d", {}, lambda _args: "ok", Access.WRITE).access is Access.WRITE


@pytest.mark.parametrize("error_type", [PolicyError, ToolFailure, ConfigError])
def test_every_error_is_a_tool_error_carrying_its_message(error_type: type[ToolError]) -> None:
    err = error_type("x")
    assert isinstance(err, ToolError)
    assert err.message == "x"
    assert str(err) == "x"
