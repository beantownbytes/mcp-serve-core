//! A small MCP server over stdio used by the integration tests and as a usage reference.

use mcp_serve_core::config::ServeConfig;
use mcp_serve_core::rmcp::model::JsonObject;
use mcp_serve_core::server::{ServerInfo, serve_stdio};
use mcp_serve_core::spec::{Access, ToolError, ToolSpec};
use serde_json::json;
use std::time::Duration;

const CONFIG_ENV: &str = "MCP_SERVE_CORE_DEMO_CONFIG";

fn schema(properties: serde_json::Value) -> JsonObject {
    let serde_json::Value::Object(schema) = json!({"type": "object", "properties": properties})
    else {
        unreachable!("json! built an object")
    };
    schema
}

fn specs() -> Vec<ToolSpec> {
    vec![
        ToolSpec::new(
            "echo",
            "Return the `text` argument.",
            schema(json!({"text": {"type": "string"}})),
            |args| async move {
                args.get("text")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .ok_or_else(|| ToolError::Tool("missing `text`".into()))
            },
        )
        .with_access(Access::ReadOnly),
        ToolSpec::new(
            "fail",
            "Always fail with a tool error.",
            schema(json!({})),
            |_| async { Err(ToolError::Tool("bad flag".into())) },
        )
        .with_access(Access::Write),
        ToolSpec::new(
            "refuse",
            "Always refuse by policy.",
            schema(json!({})),
            |_| async { Err(ToolError::Policy("not allowed".into())) },
        )
        .with_access(Access::DestructiveWrite),
        ToolSpec::new(
            "sleep",
            "Sleep for ten seconds, then return.",
            schema(json!({})),
            |_| async {
                tokio::time::sleep(Duration::from_secs(10)).await;
                Ok("woke".into())
            },
        )
        .with_access(Access::ReadOnly),
        ToolSpec::new(
            "touch",
            "Create the file at `path` after `delay_ms` milliseconds (default 1500).",
            schema(json!({"path": {"type": "string"}, "delay_ms": {"type": "integer"}})),
            |args| async move {
                let path = args
                    .get("path")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .ok_or_else(|| ToolError::Tool("missing `path`".into()))?;
                let delay_ms = args
                    .get("delay_ms")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(1500);
                tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                std::fs::write(&path, "")
                    .map_err(|e| ToolError::Tool(format!("write {path}: {e}")))?;
                Ok("touched".into())
            },
        )
        .with_access(Access::Write),
        ToolSpec::new(
            "big",
            "Return a large multibyte text.",
            schema(json!({})),
            |_| async { Ok("\u{20ac}".repeat(2000)) },
        )
        .with_access(Access::ReadOnly),
    ]
}

#[tokio::main]
async fn main() {
    let result = match ServeConfig::load("demo", CONFIG_ENV) {
        Ok(config) => {
            let info = ServerInfo::new(
                "demo",
                env!("CARGO_PKG_VERSION"),
                "Demo tools for mcp-serve-core.",
            );
            serve_stdio(info, specs(), config).await
        }
        Err(e) => Err(e),
    };
    if let Err(e) = result {
        eprintln!("demo: {e}");
        std::process::exit(1);
    }
}
