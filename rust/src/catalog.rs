//! Descriptor catalog: shaping the served tool list from configuration.

use crate::config::ToolConfig;
use crate::spec::ToolError;
use rmcp::model::Tool;

/// The entries of `names` that no tool in `tools` carries.
pub fn unknown_names<'a>(names: impl Iterator<Item = &'a String>, tools: &[Tool]) -> Vec<&'a str> {
    names
        .filter(|n| !tools.iter().any(|t| t.name == n.as_str()))
        .map(String::as_str)
        .collect()
}

/// Apply the visible allowlist and config: keep only allowlisted tools, drop hidden ones,
/// replace descriptions, and sort by name for a stable `tools/list`.
///
/// Every name the config or allowlist mentions must exist in `tools`, so a renamed or removed
/// tool fails loudly instead of silently disappearing from the served set.
///
/// # Errors
/// Returns [`ToolError::Config`] naming the unknown tools when `cfg.hide`,
/// `cfg.descriptions` or `visible` mention a tool that is not in `tools`.
pub fn shape(
    mut tools: Vec<Tool>,
    cfg: &ToolConfig,
    visible: Option<&[String]>,
) -> Result<Vec<Tool>, ToolError> {
    let unknown = unknown_names(cfg.hide.iter().chain(cfg.descriptions.keys()), &tools);
    if !unknown.is_empty() {
        return Err(ToolError::Config(format!(
            "config names tools not in the catalog: {}",
            unknown.join(", ")
        )));
    }
    if let Some(visible) = visible {
        let missing = unknown_names(visible.iter(), &tools);
        if !missing.is_empty() {
            return Err(ToolError::Config(format!(
                "visible tools not in the catalog: {}",
                missing.join(", ")
            )));
        }
        tools.retain(|t| visible.iter().any(|v| t.name == v.as_str()));
    }
    tools.retain(|t| !cfg.hide.iter().any(|h| h == t.name.as_ref()));
    for t in &mut tools {
        if let Some(d) = cfg.descriptions.get(t.name.as_ref()) {
            t.description = Some(d.clone().into());
        }
    }
    tools.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(tools)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::Access;
    use rmcp::model::JsonObject;
    use std::sync::Arc;

    fn tool(name: &'static str, description: &'static str, access: Option<Access>) -> Tool {
        let schema: JsonObject = serde_json::from_str(r#"{"type":"object"}"#).unwrap();
        let tool = Tool::new(name, description, Arc::new(schema));
        match access {
            Some(access) => tool.annotate(access.annotations()),
            None => tool,
        }
    }

    fn catalog() -> Vec<Tool> {
        vec![tool("b", "B", Some(Access::ReadOnly)), tool("a", "A", None)]
    }

    #[test]
    fn shape_hides_overrides_and_sorts() {
        let cfg = ToolConfig {
            hide: vec!["b".into()],
            descriptions: [("a".to_string(), "short".to_string())].into(),
            ..Default::default()
        };
        let tools = shape(catalog(), &cfg, None).unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0].name, "a");
        assert_eq!(tools[0].description.as_deref(), Some("short"));
    }

    #[test]
    fn shape_sorts_by_name() {
        let tools = shape(catalog(), &ToolConfig::default(), None).unwrap();
        let names: Vec<_> = tools.iter().map(|t| t.name.to_string()).collect();
        assert_eq!(names, ["a", "b"]);
    }

    #[test]
    fn config_naming_an_unknown_tool_is_an_error() {
        let cfg = ToolConfig {
            hide: vec!["renamed_upstream".into(), "gone".into()],
            ..Default::default()
        };
        let err = shape(catalog(), &cfg, None).unwrap_err();
        assert_eq!(
            err,
            ToolError::Config(
                "config names tools not in the catalog: renamed_upstream, gone".to_string()
            )
        );
        let described = ToolConfig {
            descriptions: [("gone".to_string(), "x".to_string())].into(),
            ..Default::default()
        };
        let err = shape(catalog(), &described, None).unwrap_err();
        assert_eq!(
            err,
            ToolError::Config("config names tools not in the catalog: gone".to_string())
        );
    }

    #[test]
    fn hiding_a_tool_the_allowlist_already_drops_is_harmless() {
        let visible = ["a".to_string()];
        let cfg = ToolConfig {
            hide: vec!["b".into()],
            ..Default::default()
        };
        let tools = shape(catalog(), &cfg, Some(&visible)).unwrap();
        assert_eq!(tools.len(), 1);
    }

    #[test]
    fn visible_allowlist_keeps_only_listed_tools() {
        let visible = ["b".to_string()];
        let tools = shape(catalog(), &ToolConfig::default(), Some(&visible)).unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0].name, "b");
    }

    #[test]
    fn visible_name_missing_from_catalog_is_an_error() {
        let visible = ["b".to_string(), "renamed_upstream".to_string()];
        let err = shape(catalog(), &ToolConfig::default(), Some(&visible)).unwrap_err();
        assert_eq!(
            err,
            ToolError::Config("visible tools not in the catalog: renamed_upstream".to_string())
        );
    }

    #[test]
    fn read_only_annotation_survives() {
        let tools = shape(catalog(), &ToolConfig::default(), None).unwrap();
        let b = tools.iter().find(|t| t.name == "b").unwrap();
        assert_eq!(b.annotations.as_ref().unwrap().read_only_hint, Some(true));
    }

    #[test]
    fn unknown_names_lists_only_absent_entries() {
        let names = ["a".to_string(), "zzz".to_string()];
        assert_eq!(unknown_names(names.iter(), &catalog()), ["zzz"]);
    }
}
