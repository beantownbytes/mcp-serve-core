"""Descriptor catalog: shaping the served tool list from configuration."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from mcp_types import Tool

from mcp_serve_core.config import ToolConfig
from mcp_serve_core.spec import ConfigError


def unknown_names(names: Iterable[str], tools: Sequence[Tool]) -> list[str]:
    """The entries of `names` that no tool in `tools` carries."""
    known = {t.name for t in tools}
    return [n for n in names if n not in known]


def shape(
    tools: Sequence[Tool], cfg: ToolConfig, visible: Sequence[str] | None = None
) -> list[Tool]:
    """Apply the visible allowlist and config to a tool list.

    Keeps only allowlisted tools, drops hidden ones, replaces descriptions, and sorts by name
    for a stable `tools/list`. Every name the config or allowlist mentions must exist in
    `tools`, so a renamed or removed tool fails loudly instead of silently disappearing.

    Args:
        tools: The full catalog.
        cfg: The tool configuration.
        visible: When given, the only tool names to keep.

    Raises:
        ConfigError: `cfg.hide`, `cfg.descriptions` or `visible` mention a tool that is not in
            `tools`.
    """
    unknown = unknown_names([*cfg.hide, *sorted(cfg.descriptions)], tools)
    if unknown:
        raise ConfigError(f"config names tools not in the catalog: {', '.join(unknown)}")
    kept = list(tools)
    if visible is not None:
        missing = unknown_names(visible, tools)
        if missing:
            raise ConfigError(f"visible tools not in the catalog: {', '.join(missing)}")
        kept = [t for t in kept if t.name in visible]
    kept = [t for t in kept if t.name not in cfg.hide]
    shaped = [
        t.model_copy(update={"description": cfg.descriptions[t.name]})
        if t.name in cfg.descriptions
        else t
        for t in kept
    ]
    return sorted(shaped, key=lambda t: t.name)
