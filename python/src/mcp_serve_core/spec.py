"""Tool registration types: ToolSpec, Access and the ToolError hierarchy."""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp_types import Tool, ToolAnnotations


class ToolError(Exception):
    """Why a tool handler failed. The subclass picks the label the model sees."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PolicyError(ToolError):
    """The call was refused by policy (rendered as `policy refusal (exit 3): `)."""


class ToolFailure(ToolError):
    """The tool ran and failed (rendered as `exit 2: `)."""


class ConfigError(ToolError):
    """The tool or server configuration is invalid (rendered as `exit 2: `)."""


class Access(enum.Enum):
    """What a tool does to its environment; drives the emitted MCP annotations."""

    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE_WRITE = "destructive_write"

    def annotations(self) -> ToolAnnotations:
        """The MCP annotations this access level emits."""
        match self:
            case Access.READ_ONLY:
                return ToolAnnotations(read_only_hint=True)
            case Access.WRITE:
                return ToolAnnotations(read_only_hint=False, destructive_hint=False)
            case Access.DESTRUCTIVE_WRITE:
                return ToolAnnotations(read_only_hint=False, destructive_hint=True)


ToolHandler = Callable[[dict[str, Any]], str | Awaitable[str]]
"""A tool handler: the call arguments in, the result text out; sync or async."""


@dataclass(frozen=True)
class ToolSpec:
    """One served tool: its descriptor and the handler that runs it.

    Args:
        name: Unique tool name.
        description: Description shown to the model.
        input_schema: JSON Schema for the call arguments.
        handler: Run for each call. Raise a ToolError subclass to report a failure.
        access: Access classification; None emits no annotations and fails
            `assert_fail_closed`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    access: Access | None = None

    def to_tool(self) -> Tool:
        """The MCP descriptor, annotated from `access`."""
        return Tool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            annotations=self.access.annotations() if self.access is not None else None,
        )
