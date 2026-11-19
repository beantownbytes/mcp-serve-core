"""Integration tests: the demo server driven over real stdio, as in rust/tests/serve_stdio.rs."""

from __future__ import annotations

import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from mcp_serve_core import TOOLS_TTL_MS, TRUNCATION_FOOTER
from mcp_serve_core.testing import by_id, handshake, session_with, session_with_budget

CONFIG_ENV = "MCP_SERVE_CORE_DEMO_CONFIG"
DEMO: list[str | Path] = [sys.executable, Path(__file__).with_name("demo_server.py")]

Message = dict[str, Any]


def call(request_id: int, name: str, arguments: dict[str, Any]) -> Message:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def list_tools(request_id: int) -> Message:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/list"}


def with_handshake(*extra: Message) -> list[Message]:
    return [*handshake(), *extra]


def text(response: Message) -> str:
    content = response.get("result", {}).get("content")
    assert content, f"no text content in {response}"
    value = content[0]["text"]
    assert isinstance(value, str), f"no text content in {response}"
    return value


def config_env(tmp_path: Path, config: str | None) -> dict[str, str]:
    if config is None:
        return {CONFIG_ENV: ""}
    path = tmp_path / "config.toml"
    path.write_text(config, encoding="utf-8")
    return {CONFIG_ENV: str(path)}


def run(requests: list[Message], tmp_path: Path, config: str | None = None) -> list[Message]:
    return session_with(DEMO, requests, config_env(tmp_path, config))


def tool_names(response: Message) -> list[str]:
    return [tool["name"] for tool in response["result"]["tools"]]


def test_handshake_with_no_config_returns_server_info_and_default_instructions(
    tmp_path: Path,
) -> None:
    init = by_id(run(handshake(), tmp_path), 1)
    assert init["result"]["protocolVersion"] == "2025-06-18", init
    assert init["result"]["serverInfo"]["name"] == "demo", init
    assert init["result"]["serverInfo"]["version"] == version("mcp-serve-core"), init
    assert init["result"]["instructions"] == "Demo tools for mcp-serve-core.", init
    assert isinstance(init["result"]["capabilities"]["tools"], dict), init


def test_tools_list_is_sorted_and_carries_access_annotations(tmp_path: Path) -> None:
    responses = run(with_handshake(list_tools(2)), tmp_path)
    listed = by_id(responses, 2)["result"]["tools"]
    assert [tool["name"] for tool in listed] == ["big", "echo", "fail", "refuse", "sleep", "touch"]

    def annotations(name: str) -> Message:
        found: Message = next(tool for tool in listed if tool["name"] == name)["annotations"]
        return found

    echo = annotations("echo")
    assert echo["readOnlyHint"] is True, echo
    assert echo.get("destructiveHint") is None, echo
    fail = annotations("fail")
    assert fail["readOnlyHint"] is False, fail
    assert fail["destructiveHint"] is False, fail
    refuse = annotations("refuse")
    assert refuse["readOnlyHint"] is False, refuse
    assert refuse["destructiveHint"] is True, refuse


def test_tools_list_carries_the_ttl_hint_on_a_handshake_era_protocol(tmp_path: Path) -> None:
    responses = run(with_handshake(list_tools(2)), tmp_path)
    assert by_id(responses, 1)["result"]["protocolVersion"] == "2025-06-18"
    assert by_id(responses, 2)["result"]["ttlMs"] == TOOLS_TTL_MS == 60000


def test_a_successful_call_returns_the_handler_text(tmp_path: Path) -> None:
    responses = run(with_handshake(call(2, "echo", {"text": "hello"})), tmp_path)
    ok = by_id(responses, 2)
    assert ok["result"]["isError"] is False, ok
    assert text(ok) == "hello"


def test_a_failing_call_is_an_is_error_result_not_a_protocol_error(tmp_path: Path) -> None:
    responses = run(with_handshake(call(2, "fail", {}), call(3, "nope", {})), tmp_path)

    failed = by_id(responses, 2)
    assert "error" not in failed, failed
    assert failed["result"]["isError"] is True, failed
    assert text(failed) == "exit 2: bad flag"

    unknown = by_id(responses, 3)
    assert "error" not in unknown, unknown
    assert unknown["result"]["isError"] is True, unknown
    assert text(unknown) == "unknown tool 'nope'"


def test_a_policy_refusal_carries_the_policy_label(tmp_path: Path) -> None:
    refused = by_id(run(with_handshake(call(2, "refuse", {})), tmp_path), 2)
    assert refused["result"]["isError"] is True, refused
    assert text(refused) == "policy refusal (exit 3): not allowed"


def test_an_unknown_client_protocol_version_is_answered_with_the_servers_newest(
    tmp_path: Path,
) -> None:
    requests: list[Message] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    ]
    assert by_id(run(requests, tmp_path), 1)["result"]["protocolVersion"] == "2025-11-25"


def test_configured_instructions_reach_the_initialize_response(tmp_path: Path) -> None:
    responses = run(handshake(), tmp_path, '[tools.demo]\ninstructions = "Echo and sleep."\n')
    assert by_id(responses, 1)["result"]["instructions"] == "Echo and sleep."


def test_a_hidden_tool_is_absent_from_the_catalog_and_unknown_on_call(tmp_path: Path) -> None:
    requests = with_handshake(list_tools(2), call(3, "echo", {"text": "hi"}))
    responses = run(requests, tmp_path, '[tools.demo]\nhide = ["echo"]\n')

    assert tool_names(by_id(responses, 2)) == ["big", "fail", "refuse", "sleep", "touch"]
    hidden = by_id(responses, 3)
    assert hidden["result"]["isError"] is True, hidden
    assert text(hidden) == "unknown tool 'echo'"


def test_a_session_without_envs_ignores_the_developers_own_config(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "fleet-mcp"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[tools.demo]\nhide = ["echo"]\n', encoding="utf-8")

    responses = session_with(
        DEMO,
        with_handshake(list_tools(2)),
        {"HOME": str(tmp_path), CONFIG_ENV: ""},
    )

    names = tool_names(by_id(responses, 2))
    assert "echo" in names, f"ambient config leaked: {names}"


def test_a_handler_past_the_configured_deadline_times_out(tmp_path: Path) -> None:
    started = time.monotonic()
    responses = run(
        with_handshake(call(2, "sleep", {})), tmp_path, "[tools.demo]\ntimeout_secs = 1\n"
    )
    elapsed = time.monotonic() - started

    timed_out = by_id(responses, 2)
    assert timed_out["result"]["isError"] is True, timed_out
    assert text(timed_out) == "timed out after 1s"
    assert elapsed < 8, f"the session waited for the handler: {elapsed:.1f}s"


@pytest.mark.parametrize("cancel_id", [2, "2"], ids=["int-id", "stringified-id"])
def test_a_client_cancellation_aborts_the_running_handler(
    tmp_path: Path, cancel_id: int | str
) -> None:
    canceled = tmp_path / "canceled"
    witness = tmp_path / "witness"
    requests = with_handshake(
        call(2, "touch", {"path": str(canceled)}),
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": cancel_id}},
        call(3, "touch", {"path": str(witness), "delay_ms": 2500}),
    )
    responses = run(requests, tmp_path)

    assert text(by_id(responses, 3)) == "touched"
    assert witness.exists(), "the session ended before the uncanceled call could finish"
    assert not canceled.exists(), f"the canceled handler ran to completion and wrote {canceled}"
    assert all(response.get("id") != 2 for response in responses), "a canceled call was answered"


def test_an_uncanceled_touch_call_writes_its_marker(tmp_path: Path) -> None:
    marker = tmp_path / "touched"
    responses = run(with_handshake(call(2, "touch", {"path": str(marker)})), tmp_path)

    assert text(by_id(responses, 2)) == "touched"
    assert marker.exists()


def test_a_session_past_its_exit_budget_kills_the_server_and_panics(tmp_path: Path) -> None:
    marker = tmp_path / "touched"
    requests = with_handshake(call(2, "touch", {"path": str(marker), "delay_ms": 3000}))
    started = time.monotonic()
    with pytest.raises(AssertionError) as raised:
        session_with_budget(DEMO, requests, config_env(tmp_path, None), 1)
    elapsed = time.monotonic() - started
    time.sleep(3)

    assert not marker.exists(), "the server outlived the helper and ran its handler to completion"
    assert "did not exit within 1 s of stdin closing" in str(raised.value)
    assert elapsed < 4, f"the helper waited for the server's own shutdown: {elapsed:.1f}s"


def test_output_past_the_cap_is_truncated_on_a_char_boundary_with_the_footer(
    tmp_path: Path,
) -> None:
    responses = run(
        with_handshake(call(2, "big", {})), tmp_path, "[tools.demo]\nmax_output_bytes = 1024\n"
    )

    big = by_id(responses, 2)
    assert big["result"]["isError"] is False, big
    assert text(big).endswith(TRUNCATION_FOOTER), f"no truncation footer on {len(text(big))} chars"
    body = text(big).removesuffix(TRUNCATION_FOOTER)
    size = len(body.encode("utf-8"))
    assert 1024 - 4 < size <= 1024, f"{size} bytes"
    assert set(body) == {chr(0x20AC)}, "cut inside a char"
