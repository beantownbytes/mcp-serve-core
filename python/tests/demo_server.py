"""A small MCP server over stdio used by the integration tests and as a usage reference."""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mcp_serve_core import (
    Access,
    ConfigError,
    PolicyError,
    ServeConfig,
    ServerInfo,
    ToolFailure,
    ToolSpec,
    serve_stdio,
)

CONFIG_ENV = "MCP_SERVE_CORE_DEMO_CONFIG"


def schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


def echo(args: dict[str, Any]) -> str:
    text = args.get("text")
    if not isinstance(text, str):
        raise ToolFailure("missing `text`")
    return text


def fail(args: dict[str, Any]) -> str:
    raise ToolFailure("bad flag")


def refuse(args: dict[str, Any]) -> str:
    raise PolicyError("not allowed")


async def sleep(args: dict[str, Any]) -> str:
    await asyncio.sleep(10)
    return "woke"


async def touch(args: dict[str, Any]) -> str:
    path = args.get("path")
    if not isinstance(path, str):
        raise ToolFailure("missing `path`")
    delay_ms = args.get("delay_ms")
    if type(delay_ms) is not int or delay_ms < 0:
        delay_ms = 1500
    await asyncio.sleep(delay_ms / 1000)
    try:
        Path(path).write_text("")
    except OSError as err:
        raise ToolFailure(f"write {path}: {err}") from err
    return "touched"


def big(args: dict[str, Any]) -> str:
    return chr(0x20AC) * 2000


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "echo",
            "Return the `text` argument.",
            schema({"text": {"type": "string"}}),
            echo,
            Access.READ_ONLY,
        ),
        ToolSpec("fail", "Always fail with a tool error.", schema({}), fail, Access.WRITE),
        ToolSpec(
            "refuse",
            "Always refuse by policy.",
            schema({}),
            refuse,
            Access.DESTRUCTIVE_WRITE,
        ),
        ToolSpec(
            "sleep",
            "Sleep for ten seconds, then return.",
            schema({}),
            sleep,
            Access.READ_ONLY,
        ),
        ToolSpec(
            "touch",
            "Create the file at `path` after `delay_ms` milliseconds (default 1500).",
            schema({"path": {"type": "string"}, "delay_ms": {"type": "integer"}}),
            touch,
            Access.WRITE,
        ),
        ToolSpec(
            "big",
            "Return a large multibyte text.",
            schema({}),
            big,
            Access.READ_ONLY,
        ),
    ]


async def main() -> None:
    config = ServeConfig.load("demo", CONFIG_ENV)
    info = ServerInfo("demo", version("mcp-serve-core"), "Demo tools for mcp-serve-core.")
    await serve_stdio(info, specs(), config)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as err:
        print(f"demo: {err}", file=sys.stderr)
        sys.exit(1)
