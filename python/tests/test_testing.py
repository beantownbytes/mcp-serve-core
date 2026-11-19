import sys
from typing import Any

import pytest

from mcp_serve_core import Access, ToolSpec
from mcp_serve_core.testing import (
    assert_fail_closed,
    by_id,
    handshake,
    session,
    session_with,
    session_with_budget,
)


def spec(name: str, access: Access | None) -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object"}, lambda _: "", access)


def python(source: str) -> list[str]:
    return [sys.executable, "-c", source]


ECHO = "import sys; sys.stdout.write(sys.stdin.read())"


@pytest.mark.parametrize("access", list(Access))
def test_a_classified_tool_passes(access: Access) -> None:
    assert_fail_closed([spec("a", access)])


def test_an_empty_registry_passes() -> None:
    assert_fail_closed([])


def test_an_unclassified_tool_fails() -> None:
    with pytest.raises(AssertionError, match=r"tools without an access classification: a$"):
        assert_fail_closed([spec("a", None)])


def test_a_duplicate_name_fails() -> None:
    with pytest.raises(AssertionError, match=r"duplicate tool names: a$"):
        assert_fail_closed([spec("a", Access.READ_ONLY), spec("a", Access.WRITE)])


def test_all_failures_are_named_in_one_error() -> None:
    specs = [
        spec("first", None),
        spec("second", None),
        spec("dup", Access.READ_ONLY),
        spec("dup", Access.READ_ONLY),
        spec("dup", Access.READ_ONLY),
    ]
    with pytest.raises(AssertionError) as caught:
        assert_fail_closed(specs)
    assert str(caught.value) == (
        "tools without an access classification: first, second; duplicate tool names: dup"
    )


def test_handshake_opens_with_initialize_id_1() -> None:
    initialize, initialized = handshake()
    assert (initialize["id"], initialize["method"]) == (1, "initialize")
    assert initialized["method"] == "notifications/initialized"
    assert "id" not in initialized


def test_by_id_finds_the_matching_response() -> None:
    responses: list[dict[str, Any]] = [{"id": 1}, {"id": 2, "x": True}]
    assert by_id(responses, 2)["x"] is True


def test_by_id_fails_listing_the_responses_when_absent() -> None:
    with pytest.raises(AssertionError, match=r"no response with id 3: \[\{'id': 1\}\]"):
        by_id([{"id": 1}], 3)


def test_session_returns_the_responses_of_a_server_that_exits_after_stdin_closes() -> None:
    requests = [*handshake(), {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    assert session(python(ECHO), requests) == requests


def test_session_gives_the_server_a_private_config_home_unless_env_sets_it() -> None:
    source = (
        "import json, os;"
        "print(json.dumps({'id': 1, 'home': os.environ['XDG_CONFIG_HOME'],"
        " 'extra': os.environ.get('EXTRA')}))"
    )
    private = by_id(session(python(source), []), 1)
    again = by_id(session(python(source), []), 1)
    assert private["home"] != again["home"]
    assert private["extra"] is None
    chosen = by_id(session_with(python(source), [], {"XDG_CONFIG_HOME": "/x", "EXTRA": "y"}), 1)
    assert (chosen["home"], chosen["extra"]) == ("/x", "y")


def test_session_fails_on_a_non_json_line() -> None:
    with pytest.raises(AssertionError, match="non-JSON-RPC line 'oops'"):
        session(python("print('oops')"), [])


def test_session_fails_carrying_stderr_on_an_unsuccessful_exit() -> None:
    source = "import sys; sys.stderr.write('kaput'); sys.exit(3)"
    with pytest.raises(AssertionError, match="status 3; stderr:\nkaput"):
        session(python(source), [])


def test_session_fails_when_the_command_cannot_be_spawned() -> None:
    with pytest.raises(AssertionError, match="spawn"):
        session(["/nonexistent/server"], [])


def test_session_kills_a_server_that_outlives_its_budget() -> None:
    with pytest.raises(AssertionError, match=r"did not exit within 0\.5 s of stdin closing"):
        session_with_budget(python("import time; time.sleep(30)"), [], None, 0.5)
