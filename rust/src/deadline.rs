//! Effective per-call deadline.

use crate::config::MAX_TIMEOUT_SECS;
use rmcp::model::Tool;
use std::time::Duration;

/// Slack added to a tool's own `timeout_seconds` so the tool reports its timeout before the
/// server abandons it.
pub const TOOL_TIMEOUT_MARGIN: Duration = Duration::from_secs(30);

/// Deadline for one call. A tool that takes its own `timeout_seconds` (for example a
/// long-poll wait tool) is allowed to run that long, from the call's argument or the schema
/// default, plus a margin; the configured timeout is the floor.
pub fn effective_timeout(
    configured: Duration,
    tool: &Tool,
    args: &serde_json::Map<String, serde_json::Value>,
) -> Duration {
    let requested = args
        .get("timeout_seconds")
        .and_then(serde_json::Value::as_u64)
        .filter(|secs| *secs > 0)
        .or_else(|| {
            tool.input_schema
                .get("properties")?
                .get("timeout_seconds")?
                .get("default")?
                .as_u64()
        });
    match requested {
        Some(secs) => {
            configured.max(Duration::from_secs(secs.min(MAX_TIMEOUT_SECS)) + TOOL_TIMEOUT_MARGIN)
        }
        None => configured,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tool_with_timeout_default(default: Option<u64>) -> Tool {
        let mut timeout = serde_json::json!({"type": "integer"});
        if let Some(d) = default {
            timeout["default"] = d.into();
        }
        serde_json::from_value(serde_json::json!({
            "name": "await_actions_run",
            "inputSchema": {"type": "object", "properties": {"timeout_seconds": timeout}},
        }))
        .unwrap()
    }

    fn args(v: serde_json::Value) -> serde_json::Map<String, serde_json::Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn deadline_clears_the_tools_own_timeout_argument() {
        let tool = tool_with_timeout_default(Some(300));
        let got = effective_timeout(
            Duration::from_secs(120),
            &tool,
            &args(serde_json::json!({"timeout_seconds": 1800})),
        );
        assert_eq!(got, Duration::from_secs(1830));
    }

    #[test]
    fn an_absurd_timeout_argument_is_clamped_instead_of_overflowing() {
        let tool = tool_with_timeout_default(Some(300));
        let got = effective_timeout(
            Duration::from_secs(120),
            &tool,
            &args(serde_json::json!({"timeout_seconds": u64::MAX})),
        );
        assert_eq!(
            got,
            Duration::from_secs(MAX_TIMEOUT_SECS) + TOOL_TIMEOUT_MARGIN
        );
    }

    #[test]
    fn a_zero_timeout_argument_means_the_tools_default_not_no_time() {
        let tool = tool_with_timeout_default(Some(300));
        let got = effective_timeout(
            Duration::from_secs(120),
            &tool,
            &args(serde_json::json!({"timeout_seconds": 0})),
        );
        assert_eq!(got, Duration::from_secs(330));
    }

    #[test]
    fn deadline_falls_back_to_the_schema_default() {
        let tool = tool_with_timeout_default(Some(300));
        let got = effective_timeout(
            Duration::from_secs(120),
            &tool,
            &args(serde_json::json!({})),
        );
        assert_eq!(got, Duration::from_secs(330));
    }

    #[test]
    fn configured_timeout_is_the_floor() {
        let short = tool_with_timeout_default(Some(10));
        let got = effective_timeout(
            Duration::from_secs(120),
            &short,
            &args(serde_json::json!({})),
        );
        assert_eq!(got, Duration::from_secs(120));
        let none = tool_with_timeout_default(None);
        let got = effective_timeout(
            Duration::from_secs(120),
            &none,
            &args(serde_json::json!({})),
        );
        assert_eq!(got, Duration::from_secs(120));
    }

    #[test]
    fn a_schema_default_of_zero_is_not_filtered() {
        let tool = tool_with_timeout_default(Some(0));
        let got = effective_timeout(Duration::from_secs(10), &tool, &args(serde_json::json!({})));
        assert_eq!(got, Duration::from_secs(30));
        let got = effective_timeout(
            Duration::from_secs(120),
            &tool,
            &args(serde_json::json!({})),
        );
        assert_eq!(got, Duration::from_secs(120));
    }
}
