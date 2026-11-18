//! Optional per-tool configuration: catalog trimming and limits.

use crate::spec::ToolError;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// Deadline used when the config does not set `timeout_secs`.
pub const DEFAULT_TIMEOUT_SECS: u64 = 120;
/// Output cap used when the config does not set `max_output_bytes`.
pub const DEFAULT_MAX_OUTPUT_BYTES: usize = 256 * 1024;
/// Ceiling on the configured or caller-supplied part of a deadline (the per-call margin is
/// added on top), so a call cannot hold a handler open indefinitely and the sum cannot
/// overflow.
pub const MAX_TIMEOUT_SECS: u64 = 2 * 60 * 60;
/// Ceiling on the output cap.
pub const MAX_OUTPUT_BYTES_CEILING: usize = 64 * 1024 * 1024;
/// Floor on the deadline, so a `0` typo cannot turn every call into an instant timeout.
pub const MIN_TIMEOUT_SECS: u64 = 1;
/// Floor on the output cap, so a `0` typo cannot truncate every call to nothing.
pub const MIN_OUTPUT_BYTES: usize = 1024;

/// The `[tools.<name>]` section of the config file.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ToolConfig {
    /// Tool names dropped from the served catalog.
    pub hide: Vec<String>,
    /// Replacement server instructions (keep to one or two lines).
    pub instructions: Option<String>,
    /// Tool name -> replacement description.
    pub descriptions: BTreeMap<String, String>,
    /// Configured deadline in seconds; read it through [`ToolConfig::timeout_secs`].
    pub timeout_secs: Option<u64>,
    /// Configured output cap in bytes; read it through [`ToolConfig::max_output_bytes`].
    pub max_output_bytes: Option<usize>,
}

impl ToolConfig {
    /// The deadline in seconds, defaulted and clamped to the floor and ceiling.
    pub fn timeout_secs(&self) -> u64 {
        self.timeout_secs
            .unwrap_or(DEFAULT_TIMEOUT_SECS)
            .clamp(MIN_TIMEOUT_SECS, MAX_TIMEOUT_SECS)
    }

    /// The output cap in bytes, defaulted and clamped to the floor and ceiling.
    pub fn max_output_bytes(&self) -> usize {
        self.max_output_bytes
            .unwrap_or(DEFAULT_MAX_OUTPUT_BYTES)
            .clamp(MIN_OUTPUT_BYTES, MAX_OUTPUT_BYTES_CEILING)
    }
}

/// Serving configuration for one tool server.
#[derive(Debug, Clone, Default)]
pub struct ServeConfig {
    /// The parsed `[tools.<name>]` section.
    pub tool: ToolConfig,
}

impl ServeConfig {
    /// Wrap an already-parsed tool section.
    pub fn new(tool: ToolConfig) -> Self {
        Self { tool }
    }

    /// Load the config for `tool_name`; see [`load`].
    ///
    /// # Errors
    /// Returns [`ToolError::Config`] when the file cannot be read or parsed.
    pub fn load(tool_name: &str, env_var: &str) -> Result<Self, ToolError> {
        load(tool_name, env_var).map(Self::new)
    }

    /// The configured per-call deadline floor.
    pub fn timeout(&self) -> Duration {
        Duration::from_secs(self.tool.timeout_secs())
    }

    /// The configured output cap in bytes.
    pub fn max_output_bytes(&self) -> usize {
        self.tool.max_output_bytes()
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct File {
    #[serde(default)]
    tools: BTreeMap<String, ToolConfig>,
}

/// Where the config lives, and whether the path was named explicitly through `env_var`.
/// A missing default file is normal; a missing explicit one is a mistake. An empty variable
/// counts as unset.
fn config_path(env_var: &str) -> Option<(PathBuf, bool)> {
    resolve_path(
        std::env::var_os(env_var),
        std::env::var_os("XDG_CONFIG_HOME"),
        std::env::var_os("HOME"),
    )
}

fn resolve_path(
    explicit: Option<OsString>,
    xdg_config_home: Option<OsString>,
    home: Option<OsString>,
) -> Option<(PathBuf, bool)> {
    if let Some(p) = explicit.filter(|v| !v.is_empty()) {
        return Some((PathBuf::from(p), true));
    }
    let base = xdg_config_home
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            home.filter(|v| !v.is_empty())
                .map(|h| PathBuf::from(h).join(".config"))
        })?;
    Some((base.join("fleet-mcp").join("config.toml"), false))
}

/// Config for `tool_name`; defaults when the default file or the section is absent. A file
/// named through the `env_var` environment variable must exist.
///
/// # Errors
/// Returns [`ToolError::Config`] when the file cannot be read or parsed.
pub fn load(tool_name: &str, env_var: &str) -> Result<ToolConfig, ToolError> {
    let Some((path, explicit)) = config_path(env_var) else {
        return Ok(ToolConfig::default());
    };
    read_config(&path, explicit, tool_name)
}

fn read_config(path: &Path, explicit: bool, tool_name: &str) -> Result<ToolConfig, ToolError> {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound && !explicit => {
            return Ok(ToolConfig::default());
        }
        Err(e) => {
            return Err(ToolError::Config(format!(
                "reading {}: {e}",
                path.display()
            )));
        }
    };
    parse(&text, tool_name)
        .map_err(|e| ToolError::Config(format!("parsing {}: {e}", path.display())))
}

/// The `[tools.<tool_name>]` section of config text; defaults when the section is absent.
fn parse(text: &str, tool_name: &str) -> Result<ToolConfig, toml::de::Error> {
    let mut file: File = toml::from_str(text)?;
    Ok(file.tools.remove(tool_name).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_section_gives_defaults() {
        let cfg = parse("[tools.other]\ntimeout_secs = 5\n", "gitea").unwrap();
        assert_eq!(cfg.timeout_secs(), DEFAULT_TIMEOUT_SECS);
        assert_eq!(cfg.max_output_bytes(), DEFAULT_MAX_OUTPUT_BYTES);
        assert!(cfg.hide.is_empty() && cfg.instructions.is_none());
    }

    #[test]
    fn every_documented_key_is_read() {
        let cfg = parse(
            "[tools.gitea]\nhide = [\"a\"]\ninstructions = \"one line\"\ntimeout_secs = 7\nmax_output_bytes = 2048\n[tools.gitea.descriptions]\nb = \"short\"\n",
            "gitea",
        )
        .unwrap();
        assert_eq!(cfg.hide, vec!["a".to_string()]);
        assert_eq!(cfg.instructions.as_deref(), Some("one line"));
        assert_eq!((cfg.timeout_secs(), cfg.max_output_bytes()), (7, 2048));
        assert_eq!(cfg.descriptions.get("b").map(String::as_str), Some("short"));
    }

    #[test]
    fn limits_are_clamped_so_a_huge_value_cannot_panic_the_deadline() {
        let cfg = parse(
            "[tools.gitea]\ntimeout_secs = 9223372036854775807\nmax_output_bytes = 9223372036854775807\n",
            "gitea",
        )
        .unwrap();
        assert_eq!(cfg.timeout_secs(), MAX_TIMEOUT_SECS);
        assert_eq!(cfg.max_output_bytes(), MAX_OUTPUT_BYTES_CEILING);
    }

    #[test]
    fn zero_limits_are_raised_to_the_floor() {
        let cfg = parse(
            "[tools.gitea]\ntimeout_secs = 0\nmax_output_bytes = 0\n",
            "gitea",
        )
        .unwrap();
        assert_eq!(cfg.timeout_secs(), MIN_TIMEOUT_SECS);
        assert_eq!(cfg.max_output_bytes(), MIN_OUTPUT_BYTES);
    }

    #[test]
    fn a_misspelled_key_is_an_error_not_a_silent_default() {
        assert!(parse("[tools.gitea]\ntimeout_sec = 5\n", "gitea").is_err());
        assert!(parse("[tool.gitea]\n", "gitea").is_err());
        assert!(parse("not toml =", "gitea").is_err());
    }

    #[test]
    fn serve_config_exposes_clamped_limits() {
        let cfg = ServeConfig::new(parse("[tools.t]\ntimeout_secs = 9\n", "t").unwrap());
        assert_eq!(cfg.timeout(), Duration::from_secs(9));
        assert_eq!(cfg.max_output_bytes(), DEFAULT_MAX_OUTPUT_BYTES);
    }

    #[test]
    fn a_missing_explicit_config_file_is_an_error_but_a_missing_default_is_not() {
        let missing = Path::new("/nonexistent/mcp-serve-core/config.toml");
        assert!(matches!(
            read_config(missing, true, "t"),
            Err(ToolError::Config(m)) if m.contains("reading")
        ));
        assert_eq!(
            read_config(missing, false, "t").unwrap().hide,
            Vec::<String>::new()
        );
    }

    #[test]
    fn an_empty_explicit_variable_falls_back_to_the_default_path() {
        let os = |s: &str| Some(OsString::from(s));
        assert_eq!(
            resolve_path(os(""), os("/x"), os("/h")),
            Some((PathBuf::from("/x/fleet-mcp/config.toml"), false))
        );
        assert_eq!(
            resolve_path(os("/c.toml"), os("/x"), os("/h")),
            Some((PathBuf::from("/c.toml"), true))
        );
        assert_eq!(
            resolve_path(None, os(""), os("/h")),
            Some((PathBuf::from("/h/.config/fleet-mcp/config.toml"), false))
        );
        assert_eq!(resolve_path(None, None, None), None);
        assert_eq!(resolve_path(None, None, os("")), None);
        assert_eq!(resolve_path(None, os(""), os("")), None);
    }
}
