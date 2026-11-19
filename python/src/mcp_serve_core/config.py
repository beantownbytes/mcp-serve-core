"""Optional per-tool configuration: catalog trimming and limits."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_serve_core.spec import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_TIMEOUT_SECS = 120
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
MAX_TIMEOUT_SECS = 2 * 60 * 60
"""Ceiling on the configured or caller-supplied part of a deadline; the per-call margin is
added on top."""
MAX_OUTPUT_BYTES_CEILING = 64 * 1024 * 1024
MIN_TIMEOUT_SECS = 1
"""Floor on the deadline, so a `0` typo cannot turn every call into an instant timeout."""
MIN_OUTPUT_BYTES = 1024
"""Floor on the output cap, so a `0` typo cannot truncate every call to nothing."""

_TOOL_KEYS = frozenset({"hide", "instructions", "descriptions", "timeout_secs", "max_output_bytes"})


@dataclass(frozen=True)
class ToolConfig:
    """The `[tools.<name>]` section of the config file.

    Args:
        hide: Tool names dropped from the served catalog.
        instructions: Replacement server instructions (keep to one or two lines).
        descriptions: Tool name to replacement description.
        timeout_secs: Configured deadline in seconds; read it through
            `effective_timeout_secs`.
        max_output_bytes: Configured output cap in bytes; read it through
            `effective_max_output_bytes`.
    """

    hide: tuple[str, ...] = ()
    instructions: str | None = None
    descriptions: dict[str, str] = field(default_factory=dict)
    timeout_secs: int | None = None
    max_output_bytes: int | None = None

    def effective_timeout_secs(self) -> int:
        """The deadline in seconds, defaulted and clamped to the floor and ceiling."""
        secs = DEFAULT_TIMEOUT_SECS if self.timeout_secs is None else self.timeout_secs
        return min(max(secs, MIN_TIMEOUT_SECS), MAX_TIMEOUT_SECS)

    def effective_max_output_bytes(self) -> int:
        """The output cap in bytes, defaulted and clamped to the floor and ceiling."""
        cap = DEFAULT_MAX_OUTPUT_BYTES if self.max_output_bytes is None else self.max_output_bytes
        return min(max(cap, MIN_OUTPUT_BYTES), MAX_OUTPUT_BYTES_CEILING)


@dataclass(frozen=True)
class ServeConfig:
    """Serving configuration for one tool server."""

    tool: ToolConfig = field(default_factory=ToolConfig)

    @classmethod
    def load(cls, tool_name: str, env_var: str) -> ServeConfig:
        """Load the config for `tool_name`; see `load`.

        Raises:
            ConfigError: The file cannot be read or parsed.
        """
        return cls(load(tool_name, env_var))

    @property
    def timeout_secs(self) -> int:
        """The configured per-call deadline floor in seconds."""
        return self.tool.effective_timeout_secs()

    @property
    def max_output_bytes(self) -> int:
        """The configured output cap in bytes."""
        return self.tool.effective_max_output_bytes()


@dataclass(frozen=True)
class ConfigPath:
    """Where the config lives, and whether `env_var` named it explicitly."""

    path: Path
    explicit: bool


def resolve_path(
    explicit: str | None, xdg_config_home: str | None, home: str | None
) -> ConfigPath | None:
    """Where the config lives, given the explicit variable, XDG_CONFIG_HOME and HOME.

    A missing default file is normal; a missing explicit one is a mistake. An empty value
    counts as unset.

    Returns:
        The path, or None when nothing names a location.
    """
    if explicit:
        return ConfigPath(Path(explicit), explicit=True)
    if xdg_config_home:
        base = Path(xdg_config_home)
    elif home:
        base = Path(home) / ".config"
    else:
        return None
    return ConfigPath(base / "fleet-mcp" / "config.toml", explicit=False)


def config_path(env_var: str) -> ConfigPath | None:
    """The config location from the process environment; see `resolve_path`."""
    return resolve_path(
        os.environ.get(env_var), os.environ.get("XDG_CONFIG_HOME"), os.environ.get("HOME")
    )


def load(tool_name: str, env_var: str) -> ToolConfig:
    """Config for `tool_name`; defaults when the default file or the section is absent.

    A file named through the `env_var` environment variable must exist.

    Raises:
        ConfigError: The file cannot be read or parsed.
    """
    located = config_path(env_var)
    if located is None:
        return ToolConfig()
    return read_config(located.path, located.explicit, tool_name)


def read_config(path: Path, explicit: bool, tool_name: str) -> ToolConfig:
    """Read the `[tools.<tool_name>]` section of the file at `path`.

    Args:
        path: The config file.
        explicit: Whether `path` was named explicitly; only then must it exist.
        tool_name: The section to return.

    Raises:
        ConfigError: The file cannot be read or parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as err:
        if explicit:
            raise ConfigError(f"reading {path}: {err}") from err
        return ToolConfig()
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigError(f"reading {path}: {err}") from err
    try:
        return parse(text, tool_name)
    except ValueError as err:
        raise ConfigError(f"parsing {path}: {err}") from err


def parse(text: str, tool_name: str) -> ToolConfig:
    """The `[tools.<tool_name>]` section of config text; defaults when it is absent.

    Raises:
        ValueError: The text is not valid TOML, or holds an unknown key or a wrongly typed
            value anywhere in the file (a tomllib.TOMLDecodeError is a ValueError).
    """
    document = tomllib.loads(text)
    unknown = sorted(set(document) - {"tools"})
    if unknown:
        raise ValueError(f"unknown field `{unknown[0]}`, expected `tools`")
    tools = _table(document.get("tools", {}), "tools")
    sections = {
        name: _tool_config(name, _table(raw, f"tools.{name}")) for name, raw in tools.items()
    }
    return sections.get(tool_name, ToolConfig())


def _table(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"`{where}` must be a table")
    return value


def _tool_config(name: str, raw: dict[str, Any]) -> ToolConfig:
    where = f"tools.{name}"
    unknown = sorted(set(raw) - _TOOL_KEYS)
    if unknown:
        raise ValueError(f"unknown field `{unknown[0]}` in `{where}`")
    return ToolConfig(
        hide=_string_list(raw.get("hide", []), f"{where}.hide"),
        instructions=_optional_str(raw.get("instructions"), f"{where}.instructions"),
        descriptions=_string_map(raw.get("descriptions", {}), f"{where}.descriptions"),
        timeout_secs=_optional_count(raw.get("timeout_secs"), f"{where}.timeout_secs"),
        max_output_bytes=_optional_count(raw.get("max_output_bytes"), f"{where}.max_output_bytes"),
    )


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"`{where}` must be an array of strings")
    return tuple(value)


def _string_map(value: object, where: str) -> dict[str, str]:
    table = _table(value, where)
    if not all(isinstance(v, str) for v in table.values()):
        raise ValueError(f"`{where}` must map names to strings")
    return dict(table)


def _optional_str(value: object, where: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"`{where}` must be a string")
    return value


def _optional_count(value: object, where: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"`{where}` must be a non-negative integer")
    return value
