"""Mapping a finished tool call to an MCP result."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_types import CallToolResult, TextContent

from mcp_serve_core.spec import ConfigError, PolicyError, ToolError

EXIT_POLICY_REFUSAL = 3
EXIT_TOOL_ERROR = 2
TRUNCATION_FOOTER = "\n[output truncated at the configured byte cap]"
PANICKED_TEXT = "tool handler panicked"

_WHITE_SPACE = "".join(
    map(
        chr,
        [
            *range(0x09, 0x0E),
            0x20,
            0x85,
            0xA0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        ],
    )
)


@dataclass(frozen=True)
class UnknownTool:
    """The name is not in the served catalog; the handler never ran."""

    name: str


@dataclass(frozen=True)
class TimedOut:
    """The handler was still running when the deadline fired."""

    seconds: int


@dataclass(frozen=True)
class Canceled:
    """The client canceled the call while the handler was still running."""


@dataclass(frozen=True)
class Panicked:
    """The handler raised an unexpected exception or returned something other than text."""


@dataclass(frozen=True)
class Success:
    """The handler returned text."""

    text: str


@dataclass(frozen=True)
class Policy:
    """The handler refused by policy."""

    message: str


@dataclass(frozen=True)
class Tool:
    """The handler failed."""

    message: str


@dataclass(frozen=True)
class Config:
    """The handler reported invalid configuration."""

    message: str


CallOutcome = UnknownTool | TimedOut | Canceled | Panicked | Success | Policy | Tool | Config
"""How one `tools/call` ended."""


def outcome_from_return(value: object) -> CallOutcome:
    """The outcome of a handler that returned `value`: Success for text, else Panicked."""
    return Success(value) if isinstance(value, str) else Panicked()


def outcome_from_exception(exc: BaseException) -> CallOutcome:
    """The outcome of a handler that raised `exc`.

    A PolicyError is a Policy outcome, a ConfigError a Config outcome, any other ToolError a
    Tool outcome, and anything else Panicked.
    """
    if isinstance(exc, PolicyError):
        return Policy(exc.message)
    if isinstance(exc, ConfigError):
        return Config(exc.message)
    if isinstance(exc, ToolError):
        return Tool(exc.message)
    return Panicked()


def cut(body: str, cap: int) -> tuple[str, bool]:
    """`body` cut at the largest UTF-8 character boundary at or below `cap` bytes.

    A negative cap counts as zero.

    Returns:
        The possibly shortened body and whether it was cut.
    """
    data = body.encode("utf-8", "surrogatepass")
    cap = max(cap, 0)
    if len(data) <= cap:
        return body, False
    end = cap
    while end > 0 and (data[end] & 0xC0) == 0x80:
        end -= 1
    return data[:end].decode("utf-8", "surrogatepass"), True


def cap_text(body: str, cap: int) -> str:
    """`body` cut at `cap` bytes with the truncation footer appended when it was cut."""
    text, truncated = cut(body, cap)
    return text + TRUNCATION_FOOTER if truncated else text


def error_text(label: str, body: str, cap: int) -> str:
    """`label` plus `body` cut at `cap` bytes and right-trimmed, then the footer if cut.

    The label and footer are not counted against the cap.
    """
    text, truncated = cut(body, cap)
    out = label + text.rstrip(_WHITE_SPACE)
    return out + TRUNCATION_FOOTER if truncated else out


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(text=text)], is_error=True)


def into_result(outcome: CallOutcome, cap: int) -> CallToolResult:
    """Map a call outcome to an MCP result.

    Failures are `isError` results, never protocol errors, so the model can read them. `cap`
    bounds the handler's body (labels and footer excluded). Unknown-tool, timeout,
    cancellation and panic results carry neither body nor footer.

    Args:
        outcome: How the call ended.
        cap: The output cap in bytes.
    """
    match outcome:
        case UnknownTool(name):
            return _error_result(f"unknown tool '{name}'")
        case TimedOut(seconds):
            return _error_result(f"timed out after {seconds}s")
        case Canceled():
            return _error_result("canceled")
        case Panicked():
            return _error_result(PANICKED_TEXT)
        case Success(text):
            return CallToolResult(content=[TextContent(text=cap_text(text, cap))], is_error=False)
        case Policy(message):
            label = f"policy refusal (exit {EXIT_POLICY_REFUSAL}): "
            return _error_result(error_text(label, message, cap))
        case Tool(message) | Config(message):
            return _error_result(error_text(f"exit {EXIT_TOOL_ERROR}: ", message, cap))
