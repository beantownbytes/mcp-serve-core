from pathlib import Path

import pytest

from mcp_serve_core import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECS,
    MAX_OUTPUT_BYTES_CEILING,
    MAX_TIMEOUT_SECS,
    MIN_OUTPUT_BYTES,
    MIN_TIMEOUT_SECS,
    ConfigError,
    ServeConfig,
)
from mcp_serve_core.config import ConfigPath, load, parse, read_config, resolve_path

HUGE = 9223372036854775807


def test_absent_section_gives_defaults() -> None:
    cfg = parse("[tools.other]\ntimeout_secs = 5\n", "gitea")
    assert cfg.effective_timeout_secs() == DEFAULT_TIMEOUT_SECS
    assert cfg.effective_max_output_bytes() == DEFAULT_MAX_OUTPUT_BYTES
    assert cfg.hide == () and cfg.instructions is None


def test_every_documented_key_is_read() -> None:
    cfg = parse(
        '[tools.gitea]\nhide = ["a"]\ninstructions = "one line"\ntimeout_secs = 7\n'
        'max_output_bytes = 2048\n[tools.gitea.descriptions]\nb = "short"\n',
        "gitea",
    )
    assert cfg.hide == ("a",)
    assert cfg.instructions == "one line"
    assert (cfg.effective_timeout_secs(), cfg.effective_max_output_bytes()) == (7, 2048)
    assert cfg.descriptions == {"b": "short"}


def test_limits_are_clamped_so_a_huge_value_cannot_overflow_the_deadline() -> None:
    cfg = parse(
        f"[tools.gitea]\ntimeout_secs = {HUGE}\nmax_output_bytes = {HUGE}\n",
        "gitea",
    )
    assert cfg.effective_timeout_secs() == MAX_TIMEOUT_SECS
    assert cfg.effective_max_output_bytes() == MAX_OUTPUT_BYTES_CEILING


def test_zero_limits_are_raised_to_the_floor() -> None:
    cfg = parse("[tools.gitea]\ntimeout_secs = 0\nmax_output_bytes = 0\n", "gitea")
    assert cfg.effective_timeout_secs() == MIN_TIMEOUT_SECS
    assert cfg.effective_max_output_bytes() == MIN_OUTPUT_BYTES


@pytest.mark.parametrize(
    "text",
    [
        "[tools.gitea]\ntimeout_sec = 5\n",
        "[tool.gitea]\n",
        "not toml =",
        "[tools.other]\nbogus = 1\n",
        "[tools.gitea]\ntimeout_secs = -1\n",
        "[tools.gitea]\ntimeout_secs = true\n",
        '[tools.gitea]\ntimeout_secs = "5"\n',
        "[tools.gitea]\nhide = [1]\n",
        "[tools.gitea]\ninstructions = 3\n",
        "[tools.gitea.descriptions]\nb = 1\n",
        "tools = 3\n",
    ],
)
def test_a_misspelled_or_mistyped_key_is_an_error_not_a_silent_default(text: str) -> None:
    with pytest.raises(ValueError):
        parse(text, "gitea")


def test_serve_config_exposes_clamped_limits() -> None:
    cfg = ServeConfig(parse("[tools.t]\ntimeout_secs = 9\n", "t"))
    assert cfg.timeout_secs == 9
    assert cfg.max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES


def test_a_missing_explicit_config_file_is_an_error_but_a_missing_default_is_not(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "nonexistent" / "config.toml"
    with pytest.raises(ConfigError, match="reading"):
        read_config(missing, True, "t")
    assert read_config(missing, False, "t").hide == ()


def test_an_unparsable_file_is_a_config_error_naming_the_path(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[tools.t]\ntimeout_sec = 5\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=f"parsing {path}"):
        read_config(path, True, "t")


def test_an_empty_explicit_variable_falls_back_to_the_default_path() -> None:
    assert resolve_path("", "/x", "/h") == ConfigPath(Path("/x/fleet-mcp/config.toml"), False)
    assert resolve_path("/c.toml", "/x", "/h") == ConfigPath(Path("/c.toml"), True)
    assert resolve_path(None, "", "/h") == ConfigPath(
        Path("/h/.config/fleet-mcp/config.toml"), False
    )
    assert resolve_path(None, None, None) is None
    assert resolve_path(None, None, "") is None
    assert resolve_path(None, "", "") is None


def test_load_reads_the_file_named_by_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[tools.t]\ntimeout_secs = 9\n", encoding="utf-8")
    monkeypatch.setenv("MCP_TEST_CONFIG", str(path))
    assert load("t", "MCP_TEST_CONFIG").timeout_secs == 9
    assert ServeConfig.load("t", "MCP_TEST_CONFIG").timeout_secs == 9


def test_load_without_any_location_gives_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_TEST_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    assert ServeConfig.load("t", "MCP_TEST_CONFIG").timeout_secs == DEFAULT_TIMEOUT_SECS


def test_load_with_a_missing_explicit_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TEST_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(ConfigError, match="reading"):
        load("t", "MCP_TEST_CONFIG")
