//! Test helpers for consumers: stdio session driving and the fail-closed annotation check.

use crate::spec::ToolSpec;
use serde_json::{Value, json};
use std::collections::HashSet;
use std::ffi::OsStr;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

const EXIT_TIMEOUT: Duration = Duration::from_secs(30);
const EXIT_POLL: Duration = Duration::from_millis(10);

/// An empty temporary config home, removed on drop.
struct ConfigHome(PathBuf);

impl Drop for ConfigHome {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn config_home() -> ConfigHome {
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "mcp-serve-core-session-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).expect("create config home");
    ConfigHome(dir)
}

/// Drive the server binary at `bin` over stdio the way a harness does and return its
/// JSON-RPC responses.
///
/// # Panics
///
/// Panics if the binary cannot be spawned, does not exit within the default exit budget of
/// [`session_with_budget`] after stdin closes (the child is killed first), exits
/// unsuccessfully (the panic carries its stderr), or writes a non-JSON-RPC line to stdout.
pub fn session(bin: &Path, requests: &[Value]) -> Vec<Value> {
    session_with(bin, requests, &[])
}

/// Like [`session`], with extra environment variables set on the server process.
///
/// The server runs with `XDG_CONFIG_HOME` pointing at an empty temporary directory unless
/// `envs` sets it, so the developer's default `fleet-mcp/config.toml` never shapes the
/// session. Only that default path is isolated: a tool whose config is named through an
/// explicit environment variable must have that variable set or blanked in `envs`, because
/// the server inherits the rest of the caller's environment.
///
/// # Panics
///
/// Panics under the same conditions as [`session`].
pub fn session_with(bin: &Path, requests: &[Value], envs: &[(&str, &OsStr)]) -> Vec<Value> {
    session_with_budget(bin, requests, envs, EXIT_TIMEOUT)
}

/// Like [`session_with`], with the time the server gets to exit after stdin closes set to
/// `exit_budget` instead of the default. [`session_with`] allows its own fixed default.
///
/// # Panics
///
/// Panics if the binary cannot be spawned, does not exit (or leaves its output pipes open)
/// within `exit_budget` of stdin closing, exits unsuccessfully (the panic carries its
/// stderr), or writes a non-JSON-RPC line to stdout. On the timeout the child is killed and
/// reaped before the panic.
pub fn session_with_budget(
    bin: &Path,
    requests: &[Value],
    envs: &[(&str, &OsStr)],
    exit_budget: Duration,
) -> Vec<Value> {
    let isolated = (!envs.iter().any(|(k, _)| *k == "XDG_CONFIG_HOME")).then(config_home);
    let mut command = Command::new(bin);
    if let Some(dir) = &isolated {
        command.env("XDG_CONFIG_HOME", &dir.0);
    }
    let mut child = command
        .envs(envs.iter().copied())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| panic!("spawn {}: {e}", bin.display()));
    let stdout = read_in_thread(child.stdout.take().expect("piped stdout"));
    let stderr = read_in_thread(child.stderr.take().expect("piped stderr"));
    let mut stdin = child.stdin.take().expect("piped stdin");
    for r in requests {
        writeln!(stdin, "{r}").expect("write request");
    }
    drop(stdin);

    let deadline = Instant::now() + exit_budget;
    let timed_out = || -> ! {
        panic!(
            "the server did not exit within {} s of stdin closing",
            exit_budget.as_secs()
        )
    };
    let status = loop {
        match child.try_wait().expect("poll the server") {
            Some(status) => break status,
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                timed_out()
            }
            None => std::thread::sleep(EXIT_POLL),
        }
    };
    let remaining = deadline.saturating_duration_since(Instant::now());
    let (Ok(stdout), Ok(stderr)) = (
        stdout.recv_timeout(remaining),
        stderr.recv_timeout(remaining),
    ) else {
        timed_out()
    };
    assert!(
        status.success(),
        "the server exited with {status}; stderr:\n{}",
        String::from_utf8_lossy(&stderr)
    );
    String::from_utf8(stdout)
        .expect("stdout is UTF-8")
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| {
            serde_json::from_str(l)
                .unwrap_or_else(|e| panic!("stdout carries a non-JSON-RPC line {l:?}: {e}"))
        })
        .collect()
}

/// Read `pipe` to EOF on its own thread and send the bytes, so a full pipe never blocks the
/// child while the caller polls for its exit.
fn read_in_thread(mut pipe: impl Read + Send + 'static) -> mpsc::Receiver<Vec<u8>> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = pipe.read_to_end(&mut bytes);
        let _ = tx.send(bytes);
    });
    rx
}

/// The `initialize` request (id 1) and `notifications/initialized` that open a session.
pub fn handshake() -> Vec<Value> {
    vec![
        json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "protocolVersion":"2025-06-18","capabilities":{},
            "clientInfo":{"name":"test","version":"0"}}}),
        json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
    ]
}

/// The response whose `id` is `id`.
///
/// # Panics
///
/// Panics, listing all responses, if none has that id.
pub fn by_id(responses: &[Value], id: u64) -> &Value {
    responses
        .iter()
        .find(|r| r["id"] == id)
        .unwrap_or_else(|| panic!("no response with id {id}: {responses:?}"))
}

/// Assert that every spec is classified and that names are unique.
///
/// An unclassified tool (`access: None`) emits no annotations, so a harness treats it as
/// a destructive write and prompts. This fails in the consumer's test instead.
///
/// # Panics
///
/// Visits every spec, then panics once naming each unclassified tool and each duplicate
/// name. An empty registry passes.
pub fn assert_fail_closed(specs: &[ToolSpec]) {
    let mut seen = HashSet::new();
    let mut unclassified = Vec::new();
    let mut duplicates: Vec<&str> = Vec::new();
    for spec in specs {
        if spec.access.is_none() {
            unclassified.push(spec.name.as_str());
        }
        if !seen.insert(spec.name.as_str()) && !duplicates.contains(&spec.name.as_str()) {
            duplicates.push(spec.name.as_str());
        }
    }
    let mut failures = Vec::new();
    if !unclassified.is_empty() {
        failures.push(format!(
            "tools without an access classification: {}",
            unclassified.join(", ")
        ));
    }
    if !duplicates.is_empty() {
        failures.push(format!("duplicate tool names: {}", duplicates.join(", ")));
    }
    assert!(failures.is_empty(), "{}", failures.join("; "));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::Access;
    use rmcp::model::JsonObject;

    fn spec(name: &str, access: Option<Access>) -> ToolSpec {
        let s = ToolSpec::new(name, "d", JsonObject::new(), |_| async {
            Ok(String::new())
        });
        match access {
            Some(a) => s.with_access(a),
            None => s,
        }
    }

    fn hints(spec: &ToolSpec) -> Option<(Option<bool>, Option<bool>)> {
        spec.to_tool()
            .annotations
            .map(|a| (a.read_only_hint, a.destructive_hint))
    }

    #[test]
    fn read_only_passes_and_emits_read_only_true() {
        let specs = [spec("a", Some(Access::ReadOnly))];
        assert_fail_closed(&specs);
        assert_eq!(hints(&specs[0]), Some((Some(true), None)));
    }

    #[test]
    fn write_passes_and_emits_not_read_only_not_destructive() {
        let specs = [spec("a", Some(Access::Write))];
        assert_fail_closed(&specs);
        assert_eq!(hints(&specs[0]), Some((Some(false), Some(false))));
    }

    #[test]
    fn destructive_write_passes_and_emits_not_read_only_destructive() {
        let specs = [spec("a", Some(Access::DestructiveWrite))];
        assert_fail_closed(&specs);
        assert_eq!(hints(&specs[0]), Some((Some(false), Some(true))));
    }

    #[test]
    fn unclassified_emits_no_annotations() {
        assert_eq!(hints(&spec("a", None)), None);
    }

    #[test]
    #[should_panic(expected = "without an access classification: a")]
    fn unclassified_tool_fails() {
        assert_fail_closed(&[spec("a", None)]);
    }

    #[test]
    #[should_panic(expected = "duplicate tool names: a")]
    fn duplicate_name_fails() {
        assert_fail_closed(&[
            spec("a", Some(Access::ReadOnly)),
            spec("a", Some(Access::Write)),
        ]);
    }

    #[test]
    fn empty_registry_passes() {
        assert_fail_closed(&[]);
    }

    fn panic_message(specs: &[ToolSpec]) -> String {
        let err =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| assert_fail_closed(specs)))
                .expect_err("must panic");
        err.downcast_ref::<String>()
            .cloned()
            .expect("formatted panic message")
    }

    #[test]
    fn all_failures_are_named_in_one_panic() {
        let msg = panic_message(&[
            spec("first", None),
            spec("second", None),
            spec("dup", Some(Access::ReadOnly)),
            spec("dup", Some(Access::ReadOnly)),
            spec("dup", Some(Access::ReadOnly)),
        ]);
        assert!(msg.contains("first, second"), "{msg}");
        assert!(msg.ends_with("duplicate tool names: dup"), "{msg}");
    }

    #[test]
    fn by_id_finds_the_matching_response() {
        let rs = [json!({"id": 1}), json!({"id": 2, "x": true})];
        assert_eq!(by_id(&rs, 2)["x"], true);
    }

    #[test]
    #[should_panic(expected = "no response with id 3")]
    fn by_id_panics_when_absent() {
        by_id(&[json!({"id": 1})], 3);
    }
}
