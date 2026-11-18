//! Tool registration types: [`ToolSpec`], [`Access`] and [`ToolError`].

use rmcp::model::{JsonObject, Tool, ToolAnnotations};
use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

/// Why a tool handler failed. The variant picks the label the model sees.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ToolError {
    /// The call was refused by policy (rendered as `policy refusal (exit 3): `).
    Policy(String),
    /// The tool ran and failed (rendered as `exit 2: `).
    Tool(String),
    /// The tool or server configuration is invalid (rendered as `exit 2: `).
    Config(String),
}

impl ToolError {
    /// The human-readable message without its label.
    pub fn message(&self) -> &str {
        match self {
            Self::Policy(m) | Self::Tool(m) | Self::Config(m) => m,
        }
    }
}

impl fmt::Display for ToolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for ToolError {}

/// What a tool does to its environment; drives the emitted MCP annotations.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    /// Does not modify its environment.
    ReadOnly,
    /// Modifies its environment additively.
    Write,
    /// May perform destructive updates.
    DestructiveWrite,
}

impl Access {
    /// The MCP annotations this access level emits.
    pub fn annotations(self) -> ToolAnnotations {
        match self {
            Self::ReadOnly => ToolAnnotations::new().read_only(true),
            Self::Write => ToolAnnotations::new().read_only(false).destructive(false),
            Self::DestructiveWrite => ToolAnnotations::new().read_only(false).destructive(true),
        }
    }
}

/// The future a tool handler returns.
pub type ToolFuture = Pin<Box<dyn Future<Output = Result<String, ToolError>> + Send>>;

/// A tool handler: the call arguments in, the result text out.
pub type ToolHandler = Arc<dyn Fn(JsonObject) -> ToolFuture + Send + Sync>;

/// One served tool: its descriptor and the handler that runs it.
#[derive(Clone)]
pub struct ToolSpec {
    /// Unique tool name.
    pub name: String,
    /// Description shown to the model.
    pub description: String,
    /// JSON Schema for the call arguments.
    pub input_schema: Arc<JsonObject>,
    /// Access classification; `None` emits no annotations and fails `assert_fail_closed`.
    pub access: Option<Access>,
    /// The handler run for each call.
    pub handler: ToolHandler,
}

impl ToolSpec {
    /// A spec with no access classification; set it with [`ToolSpec::with_access`].
    pub fn new<F, Fut>(
        name: impl Into<String>,
        description: impl Into<String>,
        input_schema: JsonObject,
        handler: F,
    ) -> Self
    where
        F: Fn(JsonObject) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<String, ToolError>> + Send + 'static,
    {
        Self {
            name: name.into(),
            description: description.into(),
            input_schema: Arc::new(input_schema),
            access: None,
            handler: Arc::new(move |args| Box::pin(handler(args))),
        }
    }

    /// Classify the tool.
    pub fn with_access(mut self, access: Access) -> Self {
        self.access = Some(access);
        self
    }

    /// The MCP descriptor, annotated from [`ToolSpec::access`].
    pub fn to_tool(&self) -> Tool {
        let tool = Tool::new(
            self.name.clone(),
            self.description.clone(),
            self.input_schema.clone(),
        );
        match self.access {
            Some(access) => tool.annotate(access.annotations()),
            None => tool,
        }
    }
}

impl fmt::Debug for ToolSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ToolSpec")
            .field("name", &self.name)
            .field("access", &self.access)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(access: Option<Access>) -> ToolSpec {
        let s = ToolSpec::new("t", "d", JsonObject::new(), |_| async { Ok(String::new()) });
        match access {
            Some(a) => s.with_access(a),
            None => s,
        }
    }

    fn hints(access: Option<Access>) -> Option<(Option<bool>, Option<bool>)> {
        spec(access)
            .to_tool()
            .annotations
            .map(|a| (a.read_only_hint, a.destructive_hint))
    }

    #[test]
    fn read_only_emits_only_the_read_only_hint() {
        assert_eq!(hints(Some(Access::ReadOnly)), Some((Some(true), None)));
    }

    #[test]
    fn write_is_not_read_only_and_not_destructive() {
        assert_eq!(hints(Some(Access::Write)), Some((Some(false), Some(false))));
    }

    #[test]
    fn destructive_write_is_not_read_only_and_destructive() {
        assert_eq!(
            hints(Some(Access::DestructiveWrite)),
            Some((Some(false), Some(true)))
        );
    }

    #[test]
    fn unclassified_emits_no_annotations() {
        assert_eq!(hints(None), None);
    }

    #[test]
    fn descriptor_carries_name_and_description() {
        let tool = spec(None).to_tool();
        assert_eq!(tool.name, "t");
        assert_eq!(tool.description.as_deref(), Some("d"));
    }

    #[test]
    fn config_and_tool_errors_share_the_message() {
        assert_eq!(ToolError::Config("x".into()).to_string(), "x");
        assert_eq!(ToolError::Policy("y".into()).message(), "y");
    }
}
