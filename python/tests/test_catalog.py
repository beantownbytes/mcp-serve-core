import pytest
from mcp_types import Tool

from mcp_serve_core import Access, ConfigError, ToolConfig, ToolSpec, shape, unknown_names


def _tool(name: str, description: str, access: Access | None = None) -> Tool:
    return ToolSpec(name, description, {"type": "object"}, lambda _args: "", access).to_tool()


def _catalog() -> list[Tool]:
    return [_tool("b", "B", Access.READ_ONLY), _tool("a", "A")]


def test_shape_hides_overrides_and_sorts() -> None:
    cfg = ToolConfig(hide=("b",), descriptions={"a": "short"})
    tools = shape(_catalog(), cfg)
    assert [t.name for t in tools] == ["a"]
    assert tools[0].description == "short"


def test_shape_does_not_mutate_the_input_catalog() -> None:
    catalog = _catalog()
    shape(catalog, ToolConfig(descriptions={"a": "short"}))
    assert [(t.name, t.description) for t in catalog] == [("b", "B"), ("a", "A")]


def test_shape_sorts_by_name() -> None:
    assert [t.name for t in shape(_catalog(), ToolConfig())] == ["a", "b"]


def test_config_naming_an_unknown_tool_is_an_error() -> None:
    cfg = ToolConfig(hide=("renamed_upstream", "gone"))
    with pytest.raises(ConfigError) as info:
        shape(_catalog(), cfg)
    assert info.value.message == "config names tools not in the catalog: renamed_upstream, gone"
    with pytest.raises(ConfigError) as described:
        shape(_catalog(), ToolConfig(descriptions={"gone": "x"}))
    assert described.value.message == "config names tools not in the catalog: gone"
    with pytest.raises(ConfigError) as ordered:
        shape(_catalog(), ToolConfig(hide=("z",), descriptions={"y": "x", "x": "x"}))
    assert ordered.value.message == "config names tools not in the catalog: z, x, y"


def test_hiding_a_tool_the_allowlist_already_drops_is_harmless() -> None:
    tools = shape(_catalog(), ToolConfig(hide=("b",)), ["a"])
    assert [t.name for t in tools] == ["a"]


def test_visible_allowlist_keeps_only_listed_tools() -> None:
    assert [t.name for t in shape(_catalog(), ToolConfig(), ["b"])] == ["b"]


def test_visible_name_missing_from_catalog_is_an_error() -> None:
    with pytest.raises(ConfigError) as info:
        shape(_catalog(), ToolConfig(), ["b", "renamed_upstream"])
    assert info.value.message == "visible tools not in the catalog: renamed_upstream"


def test_read_only_annotation_survives() -> None:
    b = next(t for t in shape(_catalog(), ToolConfig()) if t.name == "b")
    assert b.annotations is not None
    assert b.annotations.read_only_hint is True


def test_unknown_names_lists_only_absent_entries() -> None:
    assert unknown_names(["a", "zzz"], _catalog()) == ["zzz"]
