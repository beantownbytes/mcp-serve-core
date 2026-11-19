"""The native MCP server: serves ToolSpecs over stdio."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import anyio
import anyio.to_thread
from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server as LowlevelServer
from mcp.server.stdio import stdio_server
from mcp.shared._stream_protocols import ReadStream, WriteStream
from mcp.shared.dispatcher import coerce_request_id
from mcp.shared.jsonrpc_dispatcher import cancelled_request_id_from_params
from mcp.shared.message import SessionMessage
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    JSONRPCError,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    ListToolsResult,
    PaginatedRequestParams,
    RequestId,
    Tool,
)

from mcp_serve_core.catalog import shape
from mcp_serve_core.config import ServeConfig
from mcp_serve_core.deadline import effective_timeout, schema_timeout_default
from mcp_serve_core.result import (
    CallOutcome,
    Canceled,
    Panicked,
    TimedOut,
    UnknownTool,
    into_result,
    outcome_from_exception,
    outcome_from_return,
)
from mcp_serve_core.spec import ConfigError, ToolHandler, ToolSpec

TOOLS_TTL_MS = 60_000
"""How long clients may cache `tools/list`; the catalog changes only when the program is
upgraded."""

_TOOLS_LIST = "tools/list"
_CANCELLED = "notifications/cancelled"
_DRAIN_POLL = 0.01


@dataclass(frozen=True)
class ServerInfo:
    """Identity a server announces in the MCP handshake.

    Args:
        name: Server name.
        version: Server version.
        instructions: Instructions shown to the model unless the config overrides them.
    """

    name: str
    version: str
    instructions: str


def _duplicate_names(specs: Iterable[ToolSpec]) -> list[str]:
    """The names more than one spec carries, sorted, each once."""
    counts = Counter(spec.name for spec in specs)
    return sorted(name for name, count in counts.items() if count > 1)


class Server:
    """An MCP server over a fixed set of tool specs."""

    def __init__(
        self, info: ServerInfo, specs: Sequence[ToolSpec], config: ServeConfig | None = None
    ) -> None:
        """Reject duplicate spec names, then shape the catalog (hide, descriptions, name order).

        Args:
            info: The identity announced in the handshake.
            specs: The tools to serve.
            config: Serving configuration; defaults when None.

        Raises:
            ConfigError: Two specs share a name, or the config names a tool that no spec
                provides.
        """
        duplicates = _duplicate_names(specs)
        if duplicates:
            raise ConfigError(f"duplicate tool names: {', '.join(duplicates)}")
        self._info = info
        self._config = ServeConfig() if config is None else config
        self._tools = shape([spec.to_tool() for spec in specs], self._config.tool)
        served = {tool.name for tool in self._tools}
        self._handlers = {spec.name: spec for spec in specs if spec.name in served}

    @property
    def tools(self) -> list[Tool]:
        """The tools this server advertises, sorted by name."""
        return list(self._tools)

    @property
    def instructions(self) -> str:
        """The configured instructions when set, else the ones in the server info."""
        configured = self._config.tool.instructions
        return self._info.instructions if configured is None else configured

    async def call(
        self, name: str, args: dict[str, Any], canceled: asyncio.Event | None = None
    ) -> CallToolResult:
        """Run one tool call to its result. Requires the asyncio event loop.

        Every outcome, including an unknown tool, a timeout, a cancellation and a handler
        failure, is an `isError` or success result rather than a protocol error. The handler
        runs on its own task until it finishes, the deadline passes or `canceled` is set; the
        last two abort it. A finished handler wins over a simultaneous deadline or
        cancellation, and the deadline wins over a simultaneous cancellation. A sync handler
        runs on a worker thread that cannot be aborted: the result returns on time, but the
        thread runs to completion.

        Args:
            name: The tool to call.
            args: The call arguments.
            canceled: Set when the client cancels the call; None for a call that cannot be
                canceled.
        """
        outcome = await self._outcome(name, args, canceled)
        return into_result(outcome, self._config.max_output_bytes)

    async def run(self) -> None:
        """Serve over the process's stdin and stdout until the client disconnects."""
        lowlevel = LowlevelServer(
            self._info.name,
            version=self._info.version,
            instructions=self.instructions,
            cache_hints={"tools/list": CacheHint(ttl_ms=TOOLS_TTL_MS)},
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )
        requests = _Requests()
        async with stdio_server() as (read_stream, write_stream):
            await lowlevel.run(
                _RecordingReadStream(read_stream, requests),
                _TtlWriteStream(write_stream, requests),
                lowlevel.create_initialization_options(),
            )

    async def _on_list_tools(
        self, _ctx: ServerRequestContext[Any], _params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=self.tools)

    async def _on_call_tool(
        self, _ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        return await self.call(params.name, params.arguments or {})

    async def _outcome(
        self, name: str, args: dict[str, Any], canceled: asyncio.Event | None
    ) -> CallOutcome:
        spec = self._handlers.get(name)
        if spec is None:
            return UnknownTool(name)
        deadline = effective_timeout(
            self._config.timeout_secs, args, schema_timeout_default(spec.input_schema)
        )
        loop = asyncio.get_running_loop()
        expires = loop.time() + deadline
        handler = asyncio.ensure_future(_invoke(spec.handler, args))
        cancel_wait = None if canceled is None else asyncio.ensure_future(canceled.wait())
        waiting: set[asyncio.Future[Any]] = {handler}
        if cancel_wait is not None:
            waiting.add(cancel_wait)
        try:
            await asyncio.wait(waiting, timeout=deadline, return_when=asyncio.FIRST_COMPLETED)
            if handler.done():
                return Panicked() if handler.cancelled() else handler.result()
            if cancel_wait is not None and cancel_wait.done() and loop.time() < expires:
                return Canceled()
            return TimedOut(deadline)
        finally:
            handler.cancel()
            if cancel_wait is not None:
                cancel_wait.cancel()


async def serve_stdio(
    info: ServerInfo, specs: Sequence[ToolSpec], config: ServeConfig | None = None
) -> None:
    """Serve `specs` over MCP on stdio until the client disconnects.

    Requires the asyncio event loop.

    The server is built first, so duplicate names and a config naming unknown tools fail
    before any I/O.

    Raises:
        ConfigError: See `Server`.
    """
    await Server(info, specs, config).run()


async def _invoke(handler: ToolHandler, args: dict[str, Any]) -> CallOutcome:
    """Run `handler` and map whatever it does to an outcome.

    An async handler is awaited on the event loop; a sync one runs on a worker thread that is
    abandoned when the task is canceled. Cancellation is not mapped: the caller decides what
    an aborted handler means.
    """
    try:
        pending: object
        if inspect.iscoroutinefunction(handler):
            pending = handler(args)
        else:
            pending = await anyio.to_thread.run_sync(
                functools.partial(handler, args), abandon_on_cancel=True
            )
        returned = await pending if inspect.isawaitable(pending) else pending
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        return outcome_from_exception(exc)
    return outcome_from_return(returned)


@dataclass
class _Requests:
    """The inbound requests the stream wrappers track for one connection."""

    tools_list: set[RequestId] = field(default_factory=set)
    """Ids of `tools/list` requests whose response still needs `ttlMs`."""

    in_flight: set[RequestId] = field(default_factory=set)
    """Ids of requests that are neither answered nor canceled."""


class _RecordingReadStream:
    """A read stream that records inbound requests and drains in-flight calls at end of input.

    The SDK answers every request still running when stdin closes with a "Connection closed"
    error. A harness that writes its requests and closes stdin would never see a result, so
    the end of input is held back until each request is answered or canceled.
    """

    def __init__(self, inner: ReadStream[SessionMessage | Exception], requests: _Requests) -> None:
        self._inner = inner
        self._requests = requests

    @property
    def last_context(self) -> contextvars.Context | None:
        """The sender context of the last item, as the wrapped stream carries it."""
        return getattr(self._inner, "last_context", None)

    def _record(self, item: SessionMessage | Exception) -> SessionMessage | Exception:
        if not isinstance(item, SessionMessage):
            return item
        message = item.message
        if isinstance(message, JSONRPCRequest):
            request_id = coerce_request_id(message.id)
            self._requests.in_flight.add(request_id)
            if message.method == _TOOLS_LIST:
                self._requests.tools_list.add(request_id)
        elif isinstance(message, JSONRPCNotification) and message.method == _CANCELLED:
            canceled = cancelled_request_id_from_params(message.params)
            if canceled is not None:
                self._requests.in_flight.discard(coerce_request_id(canceled))
        return item

    async def _drain(self) -> None:
        while self._requests.in_flight:
            await anyio.sleep(_DRAIN_POLL)

    async def receive(self) -> SessionMessage | Exception:
        try:
            return self._record(await self._inner.receive())
        except anyio.EndOfStream:
            await self._drain()
            raise

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> _RecordingReadStream:
        return self

    async def __anext__(self) -> SessionMessage | Exception:
        try:
            return self._record(await self._inner.__anext__())
        except StopAsyncIteration:
            await self._drain()
            raise

    async def __aenter__(self) -> _RecordingReadStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()


class _TtlWriteStream:
    """A write stream that adds `ttlMs` to the response of every recorded `tools/list` request.

    WORKAROUND: the SDK drops `ttlMs` from `tools/list` results on handshake-era protocol
    versions (before 2026-07-28), where the field is not part of the schema. Remove this
    shim when the SDK serializes a handler's `ttl_ms` on every protocol version.
    """

    def __init__(self, inner: WriteStream[SessionMessage], requests: _Requests) -> None:
        self._inner = inner
        self._requests = requests

    def _stamp(self, item: SessionMessage) -> SessionMessage:
        message = item.message
        if not isinstance(message, JSONRPCResponse):
            return item
        request_id = coerce_request_id(message.id)
        if request_id not in self._requests.tools_list:
            return item
        self._requests.tools_list.discard(request_id)
        if "ttlMs" in message.result:
            return item
        stamped = message.model_copy(update={"result": {**message.result, "ttlMs": TOOLS_TTL_MS}})
        return SessionMessage(stamped, item.metadata)

    async def send(self, item: SessionMessage, /) -> None:
        try:
            await self._inner.send(self._stamp(item))
        finally:
            message = item.message
            if isinstance(message, JSONRPCResponse | JSONRPCError) and message.id is not None:
                request_id = coerce_request_id(message.id)
                self._requests.in_flight.discard(request_id)
                self._requests.tools_list.discard(request_id)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _TtlWriteStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()
