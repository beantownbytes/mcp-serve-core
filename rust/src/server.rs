//! The native MCP server: serves [`ToolSpec`]s over stdio.

use crate::catalog;
use crate::config::ServeConfig;
use crate::deadline::effective_timeout;
use crate::result::{CallOutcome, into_result};
use crate::spec::{ToolError, ToolSpec};
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, JsonObject, ListToolsResult,
    PaginatedRequestParams, ServerCapabilities, ServerConfig, Tool,
};
use rmcp::service::RequestContext;
use rmcp::transport::stdio;
use rmcp::{ErrorData as McpError, RoleServer, ServerHandler, ServiceExt};
use std::collections::{BTreeSet, HashMap};
use std::sync::Arc;

/// How long clients may cache `tools/list`; the catalog changes only when the binary is upgraded.
const TOOLS_TTL_MS: u64 = 60_000;

/// Identity a server announces in the MCP handshake.
#[derive(Debug, Clone)]
pub struct ServerInfo {
    /// Server name.
    pub name: String,
    /// Server version.
    pub version: String,
    /// Instructions shown to the model unless the config overrides them.
    pub instructions: String,
}

impl ServerInfo {
    /// Identity from its three parts.
    pub fn new(
        name: impl Into<String>,
        version: impl Into<String>,
        instructions: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            version: version.into(),
            instructions: instructions.into(),
        }
    }
}

/// An MCP server over a fixed set of tool specs.
pub struct Server {
    info: ServerInfo,
    config: ServeConfig,
    tools: Vec<Tool>,
    handlers: HashMap<String, ToolSpec>,
}

impl Server {
    /// Build a server: reject duplicate spec names, then shape the catalog (hide, descriptions,
    /// name order) from `config`.
    ///
    /// # Errors
    /// Returns [`ToolError::Config`] when two specs share a name or the config names a tool
    /// that no spec provides.
    pub fn new(
        info: ServerInfo,
        specs: Vec<ToolSpec>,
        config: ServeConfig,
    ) -> Result<Self, ToolError> {
        let mut seen = BTreeSet::new();
        let mut duplicates = BTreeSet::new();
        for spec in &specs {
            if !seen.insert(spec.name.as_str()) {
                duplicates.insert(spec.name.as_str());
            }
        }
        if !duplicates.is_empty() {
            return Err(ToolError::Config(format!(
                "duplicate tool names: {}",
                duplicates.into_iter().collect::<Vec<_>>().join(", ")
            )));
        }
        let tools = catalog::shape(
            specs.iter().map(ToolSpec::to_tool).collect(),
            &config.tool,
            None,
        )?;
        let handlers = specs.into_iter().map(|s| (s.name.clone(), s)).collect();
        Ok(Self {
            info,
            config,
            tools,
            handlers,
        })
    }

    /// The tools this server advertises, sorted by name.
    pub fn tools(&self) -> &[Tool] {
        &self.tools
    }

    fn instructions(&self) -> String {
        self.config
            .tool
            .instructions
            .clone()
            .unwrap_or_else(|| self.info.instructions.clone())
    }

    /// Run one tool call to its result. Every outcome, including an unknown tool, a timeout, a
    /// cancellation and a panic, is an `isError` or success result rather than a protocol error.
    async fn call(
        &self,
        name: &str,
        args: JsonObject,
        canceled: impl Future<Output = ()>,
    ) -> CallToolResult {
        let outcome = self.run(name, args, canceled).await;
        into_result(outcome, self.config.max_output_bytes())
    }

    /// Run the handler on its own task until it finishes, the deadline fires or `canceled`
    /// completes. The deadline and `canceled` arms abort the task, which stops at its next
    /// await point. A finished handler wins over a simultaneous deadline or cancellation.
    async fn run(
        &self,
        name: &str,
        args: JsonObject,
        canceled: impl Future<Output = ()>,
    ) -> CallOutcome {
        let (Some(tool), Some(spec)) = (
            self.tools.iter().find(|t| t.name == name),
            self.handlers.get(name),
        ) else {
            return CallOutcome::UnknownTool(name.to_string());
        };
        let deadline = effective_timeout(self.config.timeout(), tool, &args);
        let handler = Arc::clone(&spec.handler);
        let mut task = tokio::spawn(async move { handler(args).await });
        tokio::select! {
            biased;
            finished = tokio::time::timeout(deadline, &mut task) => match finished {
                Err(_elapsed) => {
                    task.abort();
                    CallOutcome::TimedOut(deadline)
                }
                Ok(Err(_join_error)) => CallOutcome::Panicked,
                Ok(Ok(result)) => CallOutcome::from(result),
            },
            () = canceled => {
                task.abort();
                CallOutcome::Canceled
            }
        }
    }
}

impl ServerHandler for Server {
    fn get_info(&self) -> ServerConfig {
        let mut info = ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_instructions(self.instructions());
        info.server_info.name = self.info.name.clone();
        info.server_info.version = self.info.version.clone();
        info
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        let mut result = ListToolsResult::default().with_ttl_ms(TOOLS_TTL_MS);
        result.tools = self.tools.clone();
        Ok(result)
    }

    fn get_tool(&self, name: &str) -> Option<Tool> {
        self.tools.iter().find(|t| t.name == name).cloned()
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, McpError> {
        let args = request.arguments.unwrap_or_default();
        Ok(self
            .call(&request.name, args, context.ct.cancelled())
            .await
            .into())
    }
}

/// Serve `specs` over MCP on stdio until the client disconnects.
///
/// The server is built first, so duplicate names and config naming unknown tools fail before
/// any I/O.
///
/// # Errors
/// Returns [`ToolError::Config`] from [`Server::new`], or [`ToolError::Tool`] when the
/// transport fails to start or ends abnormally.
pub async fn serve_stdio(
    info: ServerInfo,
    specs: Vec<ToolSpec>,
    config: ServeConfig,
) -> Result<(), ToolError> {
    let server = Server::new(info, specs, config)?;
    let service = server
        .serve(stdio())
        .await
        .map_err(|e| ToolError::Tool(format!("starting MCP service: {e}")))?;
    service
        .waiting()
        .await
        .map_err(|e| ToolError::Tool(format!("MCP service ended abnormally: {e}")))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::ToolConfig;
    use crate::result::TRUNCATION_FOOTER;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::time::Duration;

    fn info() -> ServerInfo {
        ServerInfo::new("core-test", "9.9.9", "default instructions")
    }

    fn text_of(r: &CallToolResult) -> String {
        r.content
            .iter()
            .filter_map(|c| c.as_text().map(|t| t.text.clone()))
            .collect()
    }

    fn echo(name: &str) -> ToolSpec {
        ToolSpec::new(name, "d", JsonObject::new(), |_| async {
            Ok("done".to_string())
        })
    }

    fn server(specs: Vec<ToolSpec>, tool: ToolConfig) -> Server {
        Server::new(info(), specs, ServeConfig::new(tool)).unwrap()
    }

    fn never() -> impl Future<Output = ()> {
        std::future::pending()
    }

    /// A tool named `slow` that sleeps for `sleep`, then sets the returned flag and answers
    /// `late`.
    fn slow(sleep: Duration) -> (ToolSpec, Arc<AtomicBool>) {
        let finished = Arc::new(AtomicBool::new(false));
        let flag = finished.clone();
        let spec = ToolSpec::new("slow", "d", JsonObject::new(), move |_| {
            let flag = flag.clone();
            async move {
                tokio::time::sleep(sleep).await;
                flag.store(true, Ordering::SeqCst);
                Ok("late".to_string())
            }
        });
        (spec, finished)
    }

    #[test]
    fn duplicate_names_are_rejected_naming_each_once() {
        let specs = vec![
            echo("b"),
            echo("a"),
            echo("b"),
            echo("a"),
            echo("b"),
            echo("c"),
        ];
        let err = Server::new(info(), specs, ServeConfig::default()).err();
        assert_eq!(
            err,
            Some(ToolError::Config("duplicate tool names: a, b".into()))
        );
    }

    #[test]
    fn config_naming_an_unknown_tool_is_rejected() {
        let tool = ToolConfig {
            hide: vec!["ghost".into()],
            ..ToolConfig::default()
        };
        let err = Server::new(info(), vec![echo("a")], ServeConfig::new(tool)).err();
        assert_eq!(
            err,
            Some(ToolError::Config(
                "config names tools not in the catalog: ghost".into()
            ))
        );
    }

    #[test]
    fn catalog_is_sorted_and_get_tool_uses_it() {
        let s = server(vec![echo("b"), echo("a")], ToolConfig::default());
        let names: Vec<_> = s.tools().iter().map(|t| t.name.to_string()).collect();
        assert_eq!(names, ["a", "b"]);
        assert!(s.get_tool("a").is_some());
        assert!(s.get_tool("zzz").is_none());
    }

    #[test]
    fn get_info_uses_identity_and_configured_instructions_override() {
        let s = server(vec![echo("a")], ToolConfig::default());
        let got = s.get_info();
        assert_eq!(got.server_info.name, "core-test");
        assert_eq!(got.server_info.version, "9.9.9");
        assert_eq!(got.instructions.as_deref(), Some("default instructions"));

        let tool = ToolConfig {
            instructions: Some("custom".into()),
            ..ToolConfig::default()
        };
        let s = server(vec![echo("a")], tool);
        assert_eq!(s.get_info().instructions.as_deref(), Some("custom"));
    }

    #[tokio::test]
    async fn hidden_tool_is_unknown_and_its_handler_never_runs() {
        let runs = Arc::new(AtomicUsize::new(0));
        let counter = runs.clone();
        let hidden = ToolSpec::new("secret", "d", JsonObject::new(), move |_| {
            let counter = counter.clone();
            async move {
                counter.fetch_add(1, Ordering::SeqCst);
                Ok(String::new())
            }
        });
        let tool = ToolConfig {
            hide: vec!["secret".into()],
            ..ToolConfig::default()
        };
        let s = server(vec![hidden, echo("a")], tool);
        for name in ["secret", "nope"] {
            let r = s.call(name, JsonObject::new(), never()).await;
            assert_eq!(r.is_error, Some(true));
            assert_eq!(text_of(&r), format!("unknown tool '{name}'"));
        }
        assert_eq!(runs.load(Ordering::SeqCst), 0);
    }

    #[tokio::test]
    async fn success_returns_the_handler_text() {
        let s = server(vec![echo("a")], ToolConfig::default());
        let r = s.call("a", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(false));
        assert_eq!(text_of(&r), "done");
    }

    #[tokio::test]
    async fn arguments_reach_the_handler() {
        let spec = ToolSpec::new("a", "d", JsonObject::new(), |args| async move {
            Ok(args["x"].to_string())
        });
        let s = server(vec![spec], ToolConfig::default());
        let mut args = JsonObject::new();
        args.insert("x".into(), 7.into());
        assert_eq!(text_of(&s.call("a", args, never()).await), "7");
    }

    #[tokio::test]
    async fn policy_and_tool_errors_carry_their_labels() {
        let policy = ToolSpec::new("p", "d", JsonObject::new(), |_| async {
            Err(ToolError::Policy("no\n".into()))
        });
        let failing = ToolSpec::new("t", "d", JsonObject::new(), |_| async {
            Err(ToolError::Tool("boom".into()))
        });
        let s = server(vec![policy, failing], ToolConfig::default());
        let r = s.call("p", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "policy refusal (exit 3): no");
        let r = s.call("t", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "exit 2: boom");
    }

    #[tokio::test]
    async fn panicking_handler_is_an_error_result() {
        let spec = ToolSpec::new("a", "d", JsonObject::new(), |_| async {
            if true {
                panic!("secret payload");
            }
            Ok(String::new())
        });
        let s = server(vec![spec], ToolConfig::default());
        let r = s.call("a", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "tool handler panicked");
    }

    #[tokio::test]
    async fn slow_handler_times_out_and_its_task_is_aborted() {
        let (spec, finished) = slow(Duration::from_millis(1500));
        let tool = ToolConfig {
            timeout_secs: Some(1),
            ..ToolConfig::default()
        };
        let s = server(vec![spec], tool);
        let r = s.call("slow", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "timed out after 1s");
        tokio::time::sleep(Duration::from_millis(1200)).await;
        assert!(!finished.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_timeout_seconds_argument_extends_the_deadline_past_the_configured_one() {
        let (spec, _finished) = slow(Duration::from_millis(1500));
        let tool = ToolConfig {
            timeout_secs: Some(1),
            ..ToolConfig::default()
        };
        let s = server(vec![spec], tool);
        let mut args = JsonObject::new();
        args.insert("timeout_seconds".into(), 1.into());
        let r = s.call("slow", args, never()).await;
        assert_eq!(r.is_error, Some(false));
        assert_eq!(text_of(&r), "late");
    }

    #[tokio::test]
    async fn a_canceled_handler_is_an_error_result_and_its_task_is_aborted() {
        let (spec, finished) = slow(Duration::from_millis(500));
        let s = server(vec![spec], ToolConfig::default());
        let canceled = tokio::time::sleep(Duration::from_millis(100));
        let r = s.call("slow", JsonObject::new(), canceled).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "canceled");
        tokio::time::sleep(Duration::from_millis(700)).await;
        assert!(!finished.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_handler_that_finishes_before_the_cancel_returns_its_own_result() {
        let (spec, finished) = slow(Duration::from_millis(100));
        let s = server(vec![spec], ToolConfig::default());
        let canceled = tokio::time::sleep(Duration::from_millis(1000));
        let r = s.call("slow", JsonObject::new(), canceled).await;
        assert_eq!(r.is_error, Some(false));
        assert_eq!(text_of(&r), "late");
        assert!(finished.load(Ordering::SeqCst));
    }

    #[tokio::test]
    async fn a_handler_that_panics_before_returning_its_future_is_an_error_result() {
        let spec = ToolSpec::new("a", "d", JsonObject::new(), |_| {
            if true {
                panic!("secret payload");
            }
            async { Ok(String::new()) }
        });
        let s = server(vec![spec], ToolConfig::default());
        let r = s.call("a", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(true));
        assert_eq!(text_of(&r), "tool handler panicked");
    }

    #[tokio::test]
    async fn output_over_the_cap_gains_the_footer() {
        let spec = ToolSpec::new("big", "d", JsonObject::new(), |_| async {
            Ok("x".repeat(2000))
        });
        let tool = ToolConfig {
            max_output_bytes: Some(1024),
            ..ToolConfig::default()
        };
        let s = server(vec![spec], tool);
        let r = s.call("big", JsonObject::new(), never()).await;
        assert_eq!(r.is_error, Some(false));
        assert_eq!(
            text_of(&r),
            format!("{}{TRUNCATION_FOOTER}", "x".repeat(1024))
        );
    }
}
