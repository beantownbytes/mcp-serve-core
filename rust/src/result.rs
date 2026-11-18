//! Mapping a finished tool call to an MCP result.

use crate::spec::ToolError;
use rmcp::model::{CallToolResult, ContentBlock};
use std::time::Duration;

/// Exit code the policy-refusal label reports.
pub const EXIT_POLICY_REFUSAL: i32 = 3;

/// Appended to a body cut at the configured byte cap.
pub const TRUNCATION_FOOTER: &str = "\n[output truncated at the configured byte cap]";

/// Exit code the tool-error and config-error labels report.
const EXIT_TOOL_ERROR: i32 = 2;

/// How one `tools/call` ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallOutcome {
    /// The name is not in the served catalog; the handler never ran.
    UnknownTool(String),
    /// The handler was still running when the deadline fired.
    TimedOut(Duration),
    /// The client canceled the call while the handler was still running.
    Canceled,
    /// The handler task panicked.
    Panicked,
    /// The handler returned text.
    Success(String),
    /// The handler refused by policy.
    Policy(String),
    /// The handler failed.
    Tool(String),
    /// The handler reported invalid configuration.
    Config(String),
}

impl From<Result<String, ToolError>> for CallOutcome {
    fn from(r: Result<String, ToolError>) -> Self {
        match r {
            Ok(text) => Self::Success(text),
            Err(ToolError::Policy(m)) => Self::Policy(m),
            Err(ToolError::Tool(m)) => Self::Tool(m),
            Err(ToolError::Config(m)) => Self::Config(m),
        }
    }
}

/// `body` cut at the largest char boundary at or below `cap`, and whether it was cut.
fn cut(body: &str, cap: usize) -> (&str, bool) {
    if body.len() <= cap {
        return (body, false);
    }
    let mut end = cap;
    while !body.is_char_boundary(end) {
        end -= 1;
    }
    (&body[..end], true)
}

fn error_result(msg: impl Into<String>) -> CallToolResult {
    CallToolResult::error(vec![ContentBlock::text(msg)])
}

/// Map a call outcome to an MCP result. Failures are `isError` results, never protocol errors,
/// so the model can read them. `cap` bounds the handler's body (labels and footer excluded);
/// a longer body is cut at a char boundary and gets [`TRUNCATION_FOOTER`]. Unknown-tool,
/// timeout, cancellation and panic results carry neither body nor footer.
pub fn into_result(outcome: CallOutcome, cap: usize) -> CallToolResult {
    match outcome {
        CallOutcome::UnknownTool(name) => error_result(format!("unknown tool '{name}'")),
        CallOutcome::TimedOut(deadline) => {
            error_result(format!("timed out after {}s", deadline.as_secs()))
        }
        CallOutcome::Canceled => error_result("canceled"),
        CallOutcome::Panicked => error_result("tool handler panicked"),
        CallOutcome::Success(text) => {
            let (body, truncated) = cut(&text, cap);
            let mut out = body.to_string();
            if truncated {
                out.push_str(TRUNCATION_FOOTER);
            }
            CallToolResult::success(vec![ContentBlock::text(out)])
        }
        CallOutcome::Policy(m) => labeled_error(
            &format!("policy refusal (exit {EXIT_POLICY_REFUSAL}): "),
            &m,
            cap,
        ),
        CallOutcome::Tool(m) | CallOutcome::Config(m) => {
            labeled_error(&format!("exit {EXIT_TOOL_ERROR}: "), &m, cap)
        }
    }
}

fn labeled_error(label: &str, body: &str, cap: usize) -> CallToolResult {
    let (body, truncated) = cut(body, cap);
    let mut out = format!("{label}{}", body.trim_end());
    if truncated {
        out.push_str(TRUNCATION_FOOTER);
    }
    error_result(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const CAP: usize = 16;

    fn text_of(r: &CallToolResult) -> String {
        r.content
            .iter()
            .filter_map(|c| c.as_text().map(|t| t.text.clone()))
            .collect::<Vec<_>>()
            .join("")
    }

    fn is_error(r: &CallToolResult) -> bool {
        r.is_error == Some(true)
    }

    #[test]
    fn s0_unknown_tool() {
        let r = into_result(CallOutcome::UnknownTool("nope".into()), CAP);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "unknown tool 'nope'");
    }

    #[test]
    fn s0_a_long_name_is_not_truncated_or_footered() {
        let name = "n".repeat(100);
        let r = into_result(CallOutcome::UnknownTool(name.clone()), CAP);
        assert_eq!(text_of(&r), format!("unknown tool '{name}'"));
    }

    #[test]
    fn s1_timeout() {
        let r = into_result(CallOutcome::TimedOut(Duration::from_secs(1)), CAP);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "timed out after 1s");
    }

    #[test]
    fn s1_timeout_has_no_footer_even_with_a_tiny_cap() {
        let r = into_result(CallOutcome::TimedOut(Duration::from_secs(7230)), 1);
        assert_eq!(text_of(&r), "timed out after 7230s");
    }

    #[test]
    fn s1_cancellation_has_no_body_or_footer_even_with_a_tiny_cap() {
        let r = into_result(CallOutcome::Canceled, 1);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "canceled");
    }

    #[test]
    fn s2_panic() {
        let r = into_result(CallOutcome::Panicked, CAP);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "tool handler panicked");
    }

    #[test]
    fn s3_success_within_the_cap() {
        let r = into_result(CallOutcome::Success("hello".into()), CAP);
        assert_eq!(r.is_error, Some(false));
        assert_eq!(text_of(&r), "hello");
    }

    #[test]
    fn s3_success_exactly_at_the_cap_is_not_truncated() {
        let body = "a".repeat(CAP);
        let r = into_result(CallOutcome::Success(body.clone()), CAP);
        assert_eq!(text_of(&r), body);
    }

    #[test]
    fn s3_truncated_success_stays_a_success_with_the_footer() {
        let r = into_result(CallOutcome::Success("a".repeat(CAP + 5)), CAP);
        assert_eq!(r.is_error, Some(false));
        assert_eq!(
            text_of(&r),
            format!(
                "{}\n[output truncated at the configured byte cap]",
                "a".repeat(CAP)
            )
        );
    }

    #[test]
    fn s3_truncation_does_not_trim_the_body() {
        let body = format!("{}   tail", "a".repeat(CAP - 3));
        let r = into_result(CallOutcome::Success(body), CAP);
        assert_eq!(
            text_of(&r),
            format!(
                "{}   \n[output truncated at the configured byte cap]",
                "a".repeat(CAP - 3)
            )
        );
    }

    #[test]
    fn s4_policy_refusal() {
        let r = into_result(CallOutcome::Policy("not allowed\n".into()), CAP);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "policy refusal (exit 3): not allowed");
    }

    #[test]
    fn s4_truncated_policy_refusal_is_trimmed_before_the_footer() {
        let body = format!("{}   tail", "a".repeat(CAP - 3));
        let r = into_result(CallOutcome::Policy(body), CAP);
        assert!(is_error(&r));
        assert_eq!(
            text_of(&r),
            format!(
                "policy refusal (exit 3): {}\n[output truncated at the configured byte cap]",
                "a".repeat(CAP - 3)
            )
        );
    }

    #[test]
    fn s5_tool_error() {
        let r = into_result(CallOutcome::Tool("boom".into()), CAP);
        assert!(is_error(&r));
        assert_eq!(text_of(&r), "exit 2: boom");
    }

    #[test]
    fn s5_truncated_tool_error() {
        let r = into_result(CallOutcome::Tool("b".repeat(CAP + 1)), CAP);
        assert!(is_error(&r));
        assert_eq!(
            text_of(&r),
            format!(
                "exit 2: {}\n[output truncated at the configured byte cap]",
                "b".repeat(CAP)
            )
        );
    }

    #[test]
    fn s6_config_error_has_the_tool_error_wire_form() {
        let tool = into_result(CallOutcome::Tool("bad".into()), CAP);
        let config = into_result(CallOutcome::Config("bad".into()), CAP);
        assert!(is_error(&config));
        assert_eq!(text_of(&config), "exit 2: bad");
        assert_eq!(text_of(&config), text_of(&tool));
    }

    #[test]
    fn s6_truncated_config_error() {
        let r = into_result(CallOutcome::Config("c".repeat(CAP + 1)), CAP);
        assert_eq!(
            text_of(&r),
            format!(
                "exit 2: {}\n[output truncated at the configured byte cap]",
                "c".repeat(CAP)
            )
        );
    }

    #[test]
    fn a_multibyte_char_straddling_the_cap_is_dropped_whole() {
        let body = format!("{}\u{e9}\u{e9}", "a".repeat(CAP - 1));
        assert_eq!(body.len(), CAP + 3);
        let r = into_result(CallOutcome::Success(body), CAP);
        assert_eq!(
            text_of(&r),
            format!(
                "{}\n[output truncated at the configured byte cap]",
                "a".repeat(CAP - 1)
            )
        );
    }

    #[test]
    fn a_multibyte_char_ending_exactly_at_the_cap_is_kept() {
        let body = format!("{}\u{e9}\u{e9}", "a".repeat(CAP - 2));
        let r = into_result(CallOutcome::Tool(body), CAP);
        assert_eq!(
            text_of(&r),
            format!(
                "exit 2: {}\u{e9}\n[output truncated at the configured byte cap]",
                "a".repeat(CAP - 2)
            )
        );
    }

    #[test]
    fn handler_results_map_to_the_matching_outcome() {
        assert_eq!(
            CallOutcome::from(Ok("x".to_string())),
            CallOutcome::Success("x".into())
        );
        assert_eq!(
            CallOutcome::from(Err(ToolError::Policy("p".into()))),
            CallOutcome::Policy("p".into())
        );
        assert_eq!(
            CallOutcome::from(Err(ToolError::Tool("t".into()))),
            CallOutcome::Tool("t".into())
        );
        assert_eq!(
            CallOutcome::from(Err(ToolError::Config("c".into()))),
            CallOutcome::Config("c".into())
        );
    }
}
