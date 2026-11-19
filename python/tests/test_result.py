import pytest

from mcp_serve_core import (
    TRUNCATION_FOOTER,
    CallOutcome,
    Canceled,
    Config,
    ConfigError,
    Panicked,
    Policy,
    PolicyError,
    Success,
    TimedOut,
    Tool,
    ToolError,
    ToolFailure,
    UnknownTool,
    cap_text,
    error_text,
    into_result,
    outcome_from_exception,
    outcome_from_return,
)
from mcp_serve_core.result import cut
from mcp_serve_core.testing import result_text

CAP = 16


def test_unknown_tool() -> None:
    result = into_result(UnknownTool("nope"), CAP)
    assert result.is_error
    assert result_text(result) == "unknown tool 'nope'"


def test_a_long_unknown_name_is_not_truncated_or_footered() -> None:
    name = "n" * 100
    assert result_text(into_result(UnknownTool(name), CAP)) == f"unknown tool '{name}'"


def test_timeout() -> None:
    result = into_result(TimedOut(1), CAP)
    assert result.is_error
    assert result_text(result) == "timed out after 1s"


def test_timeout_has_no_footer_even_with_a_tiny_cap() -> None:
    assert result_text(into_result(TimedOut(7230), 1)) == "timed out after 7230s"


def test_cancellation_has_no_body_or_footer_even_with_a_tiny_cap() -> None:
    result = into_result(Canceled(), 1)
    assert result.is_error
    assert result_text(result) == "canceled"


def test_panic() -> None:
    result = into_result(Panicked(), CAP)
    assert result.is_error
    assert result_text(result) == "tool handler panicked"


def test_success_within_the_cap() -> None:
    result = into_result(Success("hello"), CAP)
    assert result.is_error is False
    assert result_text(result) == "hello"


def test_success_exactly_at_the_cap_is_not_truncated() -> None:
    body = "a" * CAP
    assert result_text(into_result(Success(body), CAP)) == body


def test_truncated_success_stays_a_success_with_the_footer() -> None:
    result = into_result(Success("a" * (CAP + 5)), CAP)
    assert result.is_error is False
    assert result_text(result) == "a" * CAP + TRUNCATION_FOOTER


def test_truncation_does_not_trim_a_success_body() -> None:
    body = "a" * (CAP - 3) + "   tail"
    assert (
        result_text(into_result(Success(body), CAP)) == "a" * (CAP - 3) + "   " + TRUNCATION_FOOTER
    )


def test_policy_refusal() -> None:
    result = into_result(Policy("not allowed\n"), CAP)
    assert result.is_error
    assert result_text(result) == "policy refusal (exit 3): not allowed"


def test_truncated_policy_refusal_is_trimmed_before_the_footer() -> None:
    body = "a" * (CAP - 3) + "   tail"
    result = into_result(Policy(body), CAP)
    assert result.is_error
    assert result_text(result) == "policy refusal (exit 3): " + "a" * (CAP - 3) + TRUNCATION_FOOTER


def test_tool_error() -> None:
    result = into_result(Tool("boom"), CAP)
    assert result.is_error
    assert result_text(result) == "exit 2: boom"


def test_truncated_tool_error() -> None:
    result = into_result(Tool("b" * (CAP + 1)), CAP)
    assert result.is_error
    assert result_text(result) == "exit 2: " + "b" * CAP + TRUNCATION_FOOTER


def test_config_error_has_the_tool_error_wire_form() -> None:
    config = into_result(Config("bad"), CAP)
    assert config.is_error
    assert result_text(config) == "exit 2: bad"
    assert result_text(config) == result_text(into_result(Tool("bad"), CAP))


def test_truncated_config_error() -> None:
    assert (
        result_text(into_result(Config("c" * (CAP + 1)), CAP))
        == "exit 2: " + "c" * CAP + TRUNCATION_FOOTER
    )


def test_a_multibyte_char_straddling_the_cap_is_dropped_whole() -> None:
    body = "a" * (CAP - 1) + "éé"
    assert len(body.encode()) == CAP + 3
    assert result_text(into_result(Success(body), CAP)) == "a" * (CAP - 1) + TRUNCATION_FOOTER


def test_a_multibyte_char_ending_exactly_at_the_cap_is_kept() -> None:
    body = "a" * (CAP - 2) + "éé"
    assert (
        result_text(into_result(Tool(body), CAP))
        == "exit 2: " + "a" * (CAP - 2) + "é" + TRUNCATION_FOOTER
    )


@pytest.mark.parametrize(
    ("body", "cap", "want"),
    [
        ("hello", 16, "hello"),
        ("hello", 5, "hello"),
        ("hello!", 5, "hello" + TRUNCATION_FOOTER),
        ("ab€", 3, "ab" + TRUNCATION_FOOTER),
        ("ab€", 5, "ab€"),
        ("\U0001f600", 3, TRUNCATION_FOOTER),
        ("x", 0, TRUNCATION_FOOTER),
        ("x", -3, TRUNCATION_FOOTER),
        ("a\udc80b", 4, "a\udc80" + TRUNCATION_FOOTER),
    ],
)
def test_cap_text(body: str, cap: int, want: str) -> None:
    assert cap_text(body, cap) == want


@pytest.mark.parametrize(
    ("body", "cap", "want"),
    [
        ("bad flag\n\n", 64, "exit 2: bad flag"),
        ("x\v\f\xa0\u2028 ", 64, "exit 2: x"),
        ("abc   def", 6, "exit 2: abc" + TRUNCATION_FOOTER),
        ("xxxxxxxxxx", 4, "exit 2: xxxx" + TRUNCATION_FOOTER),
        ("ab€", 3, "exit 2: ab" + TRUNCATION_FOOTER),
        ("x\x1c", 64, "exit 2: x\x1c"),
    ],
)
def test_error_text(body: str, cap: int, want: str) -> None:
    assert error_text("exit 2: ", body, cap) == want


def test_cut_reports_whether_it_cut() -> None:
    assert cut("abc", 3) == ("abc", False)
    assert cut("abcd", 3) == ("abc", True)


def test_handler_returns_map_to_the_matching_outcome() -> None:
    assert outcome_from_return("x") == Success("x")
    assert outcome_from_return(None) == Panicked()
    assert outcome_from_return(b"x") == Panicked()


@pytest.mark.parametrize(
    ("exc", "want"),
    [
        (PolicyError("p"), Policy("p")),
        (ToolFailure("t"), Tool("t")),
        (ConfigError("c"), Config("c")),
        (ToolError("e"), Tool("e")),
        (ValueError("v"), Panicked()),
        (KeyboardInterrupt(), Panicked()),
    ],
)
def test_handler_exceptions_map_to_the_matching_outcome(
    exc: BaseException, want: CallOutcome
) -> None:
    assert outcome_from_exception(exc) == want
