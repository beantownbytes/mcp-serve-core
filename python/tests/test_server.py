import asyncio
import threading
import time
from typing import Any

import pytest

from mcp_serve_core import (
    TRUNCATION_FOOTER,
    ConfigError,
    PolicyError,
    ServeConfig,
    Server,
    ServerInfo,
    ToolConfig,
    ToolError,
    ToolFailure,
    ToolHandler,
    ToolSpec,
)
from mcp_serve_core.server import _duplicate_names
from mcp_serve_core.testing import result_text

INFO = ServerInfo("core-test", "9.9.9", "default instructions")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def spec(name: str, handler: ToolHandler | None = None, **schema: Any) -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object", **schema}, handler or (lambda _: "done"))


def server(specs: list[ToolSpec], tool: ToolConfig | None = None) -> Server:
    return Server(INFO, specs, ServeConfig(tool or ToolConfig()))


def raising(error: BaseException) -> ToolHandler:
    def handler(_args: dict[str, Any]) -> str:
        raise error

    return handler


def test_duplicate_names_are_rejected_naming_each_once() -> None:
    specs = [spec(n) for n in ["b", "a", "b", "a", "b", "c"]]
    assert _duplicate_names(specs) == ["a", "b"]
    with pytest.raises(ConfigError) as caught:
        Server(INFO, specs)
    assert caught.value.message == "duplicate tool names: a, b"


def test_duplicates_are_reported_before_the_config_is_checked() -> None:
    tool = ToolConfig(hide=("ghost",))
    with pytest.raises(ConfigError) as caught:
        server([spec("a"), spec("a")], tool)
    assert caught.value.message == "duplicate tool names: a"


def test_config_naming_an_unknown_tool_is_rejected() -> None:
    with pytest.raises(ConfigError) as caught:
        server([spec("a")], ToolConfig(hide=("ghost",)))
    assert caught.value.message == "config names tools not in the catalog: ghost"


def test_catalog_is_sorted() -> None:
    assert [t.name for t in server([spec("b"), spec("a")]).tools] == ["a", "b"]


def test_catalog_is_shaped_by_the_config() -> None:
    tool = ToolConfig(hide=("b",), descriptions={"a": "custom"})
    tools = server([spec("b"), spec("a")], tool).tools
    assert [(t.name, t.description) for t in tools] == [("a", "custom")]


def test_instructions_come_from_the_info_unless_the_config_overrides_them() -> None:
    assert server([spec("a")]).instructions == "default instructions"
    assert server([spec("a")], ToolConfig(instructions="custom")).instructions == "custom"


@pytest.mark.anyio
async def test_hidden_tool_is_unknown_and_its_handler_never_runs() -> None:
    runs: list[str] = []

    def secret(_args: dict[str, Any]) -> str:
        runs.append("secret")
        return ""

    s = server([spec("secret", secret), spec("a")], ToolConfig(hide=("secret",)))
    for name in ["secret", "nope"]:
        result = await s.call(name, {})
        assert result.is_error is True
        assert result_text(result) == f"unknown tool '{name}'"
    assert runs == []


@pytest.mark.anyio
async def test_success_returns_the_handler_text_for_sync_and_async_handlers() -> None:
    async def later(_args: dict[str, Any]) -> str:
        await asyncio.sleep(0)
        return "async done"

    s = server([spec("a"), spec("b", later)])
    sync_result = await s.call("a", {})
    assert (sync_result.is_error, result_text(sync_result)) == (False, "done")
    async_result = await s.call("b", {})
    assert (async_result.is_error, result_text(async_result)) == (False, "async done")


@pytest.mark.anyio
async def test_a_sync_handler_returning_an_awaitable_is_awaited() -> None:
    async def inner() -> str:
        return "awaited"

    s = server([spec("a", lambda _: inner())])
    assert result_text(await s.call("a", {})) == "awaited"


@pytest.mark.anyio
async def test_arguments_reach_the_handler() -> None:
    s = server([spec("a", lambda args: str(args["x"]))])
    assert result_text(await s.call("a", {"x": 7})) == "7"


@pytest.mark.anyio
async def test_tool_errors_carry_their_labels() -> None:
    s = server(
        [
            spec("p", raising(PolicyError("no\n"))),
            spec("t", raising(ToolFailure("boom"))),
            spec("c", raising(ConfigError("bad"))),
            spec("e", raising(ToolError("base"))),
        ]
    )
    expected = {
        "p": "policy refusal (exit 3): no",
        "t": "exit 2: boom",
        "c": "exit 2: bad",
        "e": "exit 2: base",
    }
    for name, text in expected.items():
        result = await s.call(name, {})
        assert (result.is_error, result_text(result)) == (True, text)


@pytest.mark.anyio
async def test_a_crashing_handler_is_an_error_result_without_its_message() -> None:
    async def crash(_args: dict[str, Any]) -> str:
        raise RuntimeError("secret payload")

    s = server([spec("sync", raising(RuntimeError("secret payload"))), spec("async", crash)])
    for name in ["sync", "async"]:
        result = await s.call(name, {})
        assert (result.is_error, result_text(result)) == (True, "tool handler panicked")


@pytest.mark.anyio
async def test_a_handler_raising_a_timeout_or_cancellation_is_a_crash() -> None:
    async def cancels(_args: dict[str, Any]) -> str:
        raise asyncio.CancelledError

    s = server(
        [spec("timeout", raising(TimeoutError("own"))), spec("cancels", cancels)],
    )
    for name in ["timeout", "cancels"]:
        result = await s.call(name, {})
        assert (result.is_error, result_text(result)) == (True, "tool handler panicked")


@pytest.mark.anyio
async def test_a_non_text_return_is_a_crash() -> None:
    s = server([spec("a", lambda _: 5)])  # type: ignore[arg-type,return-value]
    result = await s.call("a", {})
    assert (result.is_error, result_text(result)) == (True, "tool handler panicked")


@pytest.mark.anyio
async def test_output_over_the_cap_gains_the_footer() -> None:
    s = server([spec("big", lambda _: "x" * 2000)], ToolConfig(max_output_bytes=1024))
    result = await s.call("big", {})
    assert result.is_error is False
    assert result_text(result) == "x" * 1024 + TRUNCATION_FOOTER


def slow_async(sleep: float, finished: list[str]) -> ToolHandler:
    async def handler(_args: dict[str, Any]) -> str:
        await asyncio.sleep(sleep)
        finished.append("slow")
        return "late"

    return handler


@pytest.mark.anyio
async def test_slow_handler_times_out_and_is_aborted() -> None:
    finished: list[str] = []
    s = server([spec("slow", slow_async(1.5, finished))], ToolConfig(timeout_secs=1))
    result = await s.call("slow", {})
    assert (result.is_error, result_text(result)) == (True, "timed out after 1s")
    await asyncio.sleep(1.2)
    assert finished == []


@pytest.mark.anyio
async def test_a_timeout_seconds_argument_extends_the_deadline_past_the_configured_one() -> None:
    finished: list[str] = []
    s = server([spec("slow", slow_async(1.2, finished))], ToolConfig(timeout_secs=1))
    result = await s.call("slow", {"timeout_seconds": 1})
    assert (result.is_error, result_text(result)) == (False, "late")


@pytest.mark.anyio
async def test_a_schema_timeout_default_extends_the_deadline() -> None:
    finished: list[str] = []
    schema = {"properties": {"timeout_seconds": {"default": 1}}}
    s = server([spec("slow", slow_async(1.2, finished), **schema)], ToolConfig(timeout_secs=1))
    assert result_text(await s.call("slow", {})) == "late"


@pytest.mark.anyio
async def test_a_canceled_handler_is_an_error_result_and_is_aborted() -> None:
    finished: list[str] = []
    s = server([spec("slow", slow_async(0.5, finished))])
    canceled = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, canceled.set)
    result = await s.call("slow", {}, canceled)
    assert (result.is_error, result_text(result)) == (True, "canceled")
    await asyncio.sleep(0.7)
    assert finished == []


@pytest.mark.anyio
async def test_a_handler_that_finishes_before_the_cancel_returns_its_own_result() -> None:
    finished: list[str] = []
    s = server([spec("slow", slow_async(0.1, finished))])
    canceled = asyncio.Event()
    asyncio.get_running_loop().call_later(1.0, canceled.set)
    result = await s.call("slow", {}, canceled)
    assert (result.is_error, result_text(result)) == (False, "late")
    assert finished == ["slow"]


@pytest.mark.anyio
async def test_a_finished_handler_beats_a_simultaneous_cancel() -> None:
    canceled = asyncio.Event()

    async def handler(_args: dict[str, Any]) -> str:
        canceled.set()
        return "own result"

    result = await server([spec("a", handler)]).call("a", {}, canceled)
    assert (result.is_error, result_text(result)) == (False, "own result")


@pytest.mark.anyio
async def test_a_finished_handler_beats_a_simultaneous_deadline() -> None:
    async def handler(_args: dict[str, Any]) -> str:
        time.sleep(1.2)
        return "own result"

    s = server([spec("a", handler)], ToolConfig(timeout_secs=1))
    result = await s.call("a", {}, asyncio.Event())
    assert (result.is_error, result_text(result)) == (False, "own result")


@pytest.mark.anyio
async def test_the_deadline_beats_a_simultaneous_cancel() -> None:
    canceled = asyncio.Event()

    async def handler(_args: dict[str, Any]) -> str:
        canceled.set()
        time.sleep(1.2)
        await asyncio.sleep(30)
        return "late"

    s = server([spec("a", handler)], ToolConfig(timeout_secs=1))
    result = await s.call("a", {}, canceled)
    assert (result.is_error, result_text(result)) == (True, "timed out after 1s")


@pytest.mark.anyio
async def test_a_blocking_sync_handler_times_out_on_time() -> None:
    release = threading.Event()

    def blocking(_args: dict[str, Any]) -> str:
        release.wait(10)
        return "late"

    s = server([spec("a", blocking)], ToolConfig(timeout_secs=1))
    started = time.monotonic()
    try:
        result = await s.call("a", {})
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert (result.is_error, result_text(result)) == (True, "timed out after 1s")
    assert elapsed < 3


@pytest.mark.anyio
async def test_a_blocking_sync_handler_is_canceled_on_time() -> None:
    release = threading.Event()

    def blocking(_args: dict[str, Any]) -> str:
        release.wait(10)
        return "late"

    canceled = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, canceled.set)
    started = time.monotonic()
    try:
        result = await server([spec("a", blocking)]).call("a", {}, canceled)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert (result.is_error, result_text(result)) == (True, "canceled")
    assert elapsed < 3


@pytest.mark.anyio
async def test_canceling_the_call_aborts_the_handler() -> None:
    finished: list[str] = []
    s = server([spec("slow", slow_async(0.5, finished))])
    call = asyncio.ensure_future(s.call("slow", {}))
    await asyncio.sleep(0.1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.sleep(0.7)
    assert finished == []
