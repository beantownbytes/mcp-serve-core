"""Test helpers for consumers: stdio session driving and the fail-closed annotation check."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

from mcp_types import CallToolResult, TextContent

from mcp_serve_core.server import _duplicate_names
from mcp_serve_core.spec import ToolSpec

EXIT_TIMEOUT = 30.0
"""Seconds a server gets to exit after stdin closes, unless the caller sets another budget."""

Command = Sequence[str | os.PathLike[str]]
Message = dict[str, Any]


def session(command: Command, requests: Sequence[Mapping[str, Any]]) -> list[Message]:
    """Drive the server started by `command` over stdio and return its JSON-RPC responses.

    Raises:
        AssertionError: The command cannot be spawned, does not exit within the default exit
            budget of `session_with_budget` after stdin closes (the child is killed first),
            exits unsuccessfully (the message carries its stderr), or writes a non-JSON line
            to stdout.
    """
    return session_with(command, requests)


def session_with(
    command: Command,
    requests: Sequence[Mapping[str, Any]],
    env: Mapping[str, str] | None = None,
) -> list[Message]:
    """Like `session`, with extra environment variables set on the server process.

    The server runs with `XDG_CONFIG_HOME` pointing at an empty temporary directory unless
    `env` sets it, so the developer's default `fleet-mcp/config.toml` never shapes the session.
    Only that default path is isolated: a tool whose config is named through an explicit
    environment variable must have that variable set or blanked in `env`, because the server
    inherits the rest of the caller's environment.

    Raises:
        AssertionError: Under the same conditions as `session`.
    """
    return session_with_budget(command, requests, env, EXIT_TIMEOUT)


def session_with_budget(
    command: Command,
    requests: Sequence[Mapping[str, Any]],
    env: Mapping[str, str] | None,
    exit_budget: float,
) -> list[Message]:
    """Like `session_with`, with the seconds the server gets to exit after stdin closes set
    to `exit_budget` instead of the default.

    Raises:
        AssertionError: The command cannot be spawned, does not exit (or leaves its output
            pipes open) within `exit_budget` of stdin closing, exits unsuccessfully (the
            message carries its stderr), or writes a non-JSON line to stdout. On the timeout
            the child is killed and reaped first.
    """
    overrides = dict(env or {})
    with tempfile.TemporaryDirectory(prefix="mcp-serve-core-session-") as config_home:
        child_env = {**os.environ}
        if "XDG_CONFIG_HOME" not in overrides:
            child_env["XDG_CONFIG_HOME"] = config_home
        child_env.update(overrides)
        stdout, stderr, returncode = _exchange(command, requests, child_env, exit_budget)
    if returncode != 0:
        raise AssertionError(f"the server exited with status {returncode}; stderr:\n{stderr}")
    return [_parse_line(line) for line in stdout.splitlines() if line.strip()]


def _exchange(
    command: Command,
    requests: Sequence[Mapping[str, Any]],
    env: Mapping[str, str],
    exit_budget: float,
) -> tuple[str, str, int]:
    try:
        child = subprocess.Popen(
            [os.fspath(part) for part in command],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError as err:
        raise AssertionError(f"spawn {list(command)}: {err}") from err
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    try:
        stdout, stderr = child.communicate(stdin, timeout=exit_budget)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        for pipe in (child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()
        raise AssertionError(
            f"the server did not exit within {exit_budget:g} s of stdin closing"
        ) from None
    return stdout, stderr, child.returncode


def _parse_line(line: str) -> Message:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as err:
        raise AssertionError(f"stdout carries a non-JSON-RPC line {line!r}: {err}") from err
    if not isinstance(parsed, dict):
        raise AssertionError(f"stdout carries a non-JSON-RPC line {line!r}")
    return parsed


def handshake() -> list[Message]:
    """The `initialize` request (id 1) and `notifications/initialized` that open a session."""
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def result_text(result: CallToolResult) -> str:
    """The concatenated text content of a tool result.

    Args:
        result: The result a `Server.call` returned.
    """
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def by_id(responses: Sequence[Message], request_id: int) -> Message:
    """The response whose `id` is `request_id`.

    Raises:
        AssertionError: No response has that id; the message lists all responses.
    """
    for response in responses:
        if response.get("id") == request_id:
            return response
    raise AssertionError(f"no response with id {request_id}: {list(responses)}")


def assert_fail_closed(specs: Sequence[ToolSpec]) -> None:
    """Assert that every spec is classified and that names are unique.

    An unclassified tool emits no read-only or destructive hint, so a harness treats it as a
    destructive write and prompts. This fails in the consumer's test instead. A tool is
    classified when its annotations say read-only, or say not read-only with an explicit
    destructive hint. An empty registry passes.

    Raises:
        AssertionError: One message naming every unclassified tool and every duplicate name.
    """
    unclassified = [spec.name for spec in specs if not _is_classified(spec)]
    duplicates = _duplicate_names(specs)
    failures: list[str] = []
    if unclassified:
        failures.append(f"tools without an access classification: {', '.join(unclassified)}")
    if duplicates:
        failures.append(f"duplicate tool names: {', '.join(duplicates)}")
    if failures:
        raise AssertionError("; ".join(failures))


def _is_classified(spec: ToolSpec) -> bool:
    annotations = spec.to_tool().annotations
    if annotations is None or annotations.read_only_hint is None:
        return False
    return annotations.read_only_hint or annotations.destructive_hint is not None
