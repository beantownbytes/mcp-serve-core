use mcp_serve_core::result::TRUNCATION_FOOTER;
use mcp_serve_core::testing::{by_id, handshake, session_with, session_with_budget};
use serde_json::{Value, json};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Once;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

const CONFIG_ENV: &str = "MCP_SERVE_CORE_DEMO_CONFIG";

/// The built `demo` example. `cargo test` builds examples only when no target filter is given,
/// so a `--test serve_stdio` run builds it here.
fn demo_bin() -> PathBuf {
    static BUILD: Once = Once::new();
    let exe = std::env::current_exe().expect("locate the test executable");
    let profile_dir = exe
        .parent()
        .and_then(Path::parent)
        .unwrap_or_else(|| panic!("{} has no target/<profile>/deps parent", exe.display()))
        .to_path_buf();
    let bin = profile_dir
        .join("examples")
        .join(format!("demo{}", std::env::consts::EXE_SUFFIX));
    BUILD.call_once(|| {
        let mut cmd = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()));
        cmd.args(["build", "--example", "demo", "--all-features"]);
        if profile_dir.file_name() == Some(OsStr::new("release")) {
            cmd.arg("--release");
        }
        let status = cmd.status().expect("run cargo build --example demo");
        assert!(status.success(), "cargo build --example demo failed");
    });
    assert!(
        bin.exists(),
        "example binary {} is missing after `cargo build --example demo`",
        bin.display()
    );
    bin
}

/// A fresh config home so the user's own `fleet-mcp/config.toml` cannot leak into a session.
struct Sandbox(PathBuf);

impl Sandbox {
    fn new() -> Self {
        static NEXT: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "mcp-serve-core-serve-stdio-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).expect("create sandbox dir");
        Self(dir)
    }

    fn with_envs<R>(&self, config: Option<&str>, f: impl FnOnce(&[(&str, &OsStr)]) -> R) -> R {
        let mut envs: Vec<(&str, &OsStr)> = vec![("XDG_CONFIG_HOME", self.0.as_os_str())];
        let path = self.0.join("config.toml");
        match config {
            Some(toml) => {
                std::fs::write(&path, toml).expect("write config");
                envs.push((CONFIG_ENV, path.as_os_str()));
            }
            None => envs.push((CONFIG_ENV, OsStr::new(""))),
        }
        f(&envs)
    }

    fn run(&self, requests: &[Value], config: Option<&str>) -> Vec<Value> {
        self.with_envs(config, |envs| session_with(&demo_bin(), requests, envs))
    }

    fn run_with_budget(
        &self,
        requests: &[Value],
        config: Option<&str>,
        exit_budget: Duration,
    ) -> Vec<Value> {
        self.with_envs(config, |envs| {
            session_with_budget(&demo_bin(), requests, envs, exit_budget)
        })
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn call(id: u64, name: &str, arguments: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"method":"tools/call",
        "params":{"name":name,"arguments":arguments}})
}

fn with_handshake(extra: Vec<Value>) -> Vec<Value> {
    let mut reqs = handshake();
    reqs.extend(extra);
    reqs
}

fn text(response: &Value) -> &str {
    response["result"]["content"][0]["text"]
        .as_str()
        .unwrap_or_else(|| panic!("no text content in {response}"))
}

#[test]
fn handshake_with_no_config_returns_server_info_and_default_instructions() {
    let responses = Sandbox::new().run(&handshake(), None);
    let init = by_id(&responses, 1);
    assert_eq!(init["result"]["protocolVersion"], "2025-06-18", "{init}");
    assert_eq!(init["result"]["serverInfo"]["name"], "demo", "{init}");
    assert_eq!(
        init["result"]["serverInfo"]["version"],
        env!("CARGO_PKG_VERSION"),
        "{init}"
    );
    assert_eq!(
        init["result"]["instructions"], "Demo tools for mcp-serve-core.",
        "{init}"
    );
    assert!(
        init["result"]["capabilities"]["tools"].is_object(),
        "{init}"
    );
}

#[test]
fn tools_list_is_sorted_and_carries_access_annotations() {
    let reqs = with_handshake(vec![json!({"jsonrpc":"2.0","id":2,"method":"tools/list"})]);
    let responses = Sandbox::new().run(&reqs, None);
    let listed = by_id(&responses, 2)["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = listed.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["big", "echo", "fail", "refuse", "sleep", "touch"]);

    let annotations =
        |name: &str| listed.iter().find(|t| t["name"] == name).unwrap()["annotations"].clone();
    let echo = annotations("echo");
    assert_eq!(echo["readOnlyHint"], true, "{echo}");
    assert!(echo["destructiveHint"].is_null(), "{echo}");
    let fail = annotations("fail");
    assert_eq!(fail["readOnlyHint"], false, "{fail}");
    assert_eq!(fail["destructiveHint"], false, "{fail}");
    let refuse = annotations("refuse");
    assert_eq!(refuse["readOnlyHint"], false, "{refuse}");
    assert_eq!(refuse["destructiveHint"], true, "{refuse}");
}

#[test]
fn a_successful_call_returns_the_handler_text() {
    let reqs = with_handshake(vec![call(2, "echo", json!({"text": "hello"}))]);
    let responses = Sandbox::new().run(&reqs, None);
    let ok = by_id(&responses, 2);
    assert_eq!(ok["result"]["isError"], false, "{ok}");
    assert_eq!(text(ok), "hello");
}

#[test]
fn a_failing_call_is_an_is_error_result_not_a_protocol_error() {
    let reqs = with_handshake(vec![call(2, "fail", json!({})), call(3, "nope", json!({}))]);
    let responses = Sandbox::new().run(&reqs, None);

    let failed = by_id(&responses, 2);
    assert!(failed.get("error").is_none(), "{failed}");
    assert_eq!(failed["result"]["isError"], true, "{failed}");
    assert_eq!(text(failed), "exit 2: bad flag");

    let unknown = by_id(&responses, 3);
    assert!(unknown.get("error").is_none(), "{unknown}");
    assert_eq!(unknown["result"]["isError"], true, "{unknown}");
    assert_eq!(text(unknown), "unknown tool 'nope'");
}

#[test]
fn a_policy_refusal_carries_the_policy_label() {
    let reqs = with_handshake(vec![call(2, "refuse", json!({}))]);
    let responses = Sandbox::new().run(&reqs, None);
    let refused = by_id(&responses, 2);
    assert_eq!(refused["result"]["isError"], true, "{refused}");
    assert_eq!(text(refused), "policy refusal (exit 3): not allowed");
}

#[test]
fn an_unknown_client_protocol_version_is_answered_with_the_servers_newest() {
    let reqs = vec![
        json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2099-01-01","capabilities":{},
        "clientInfo":{"name":"test","version":"0"}}}),
    ];
    let responses = Sandbox::new().run(&reqs, None);
    assert_eq!(
        by_id(&responses, 1)["result"]["protocolVersion"],
        "2025-11-25"
    );
}

#[test]
fn configured_instructions_reach_the_initialize_response() {
    let responses = Sandbox::new().run(
        &handshake(),
        Some("[tools.demo]\ninstructions = \"Echo and sleep.\"\n"),
    );
    assert_eq!(
        by_id(&responses, 1)["result"]["instructions"],
        "Echo and sleep."
    );
}

#[test]
fn a_hidden_tool_is_absent_from_the_catalog_and_unknown_on_call() {
    let sandbox = Sandbox::new();
    let reqs = with_handshake(vec![
        json!({"jsonrpc":"2.0","id":2,"method":"tools/list"}),
        call(3, "echo", json!({"text": "hi"})),
    ]);
    let responses = sandbox.run(&reqs, Some("[tools.demo]\nhide = [\"echo\"]\n"));

    let names: Vec<&str> = by_id(&responses, 2)["result"]["tools"]
        .as_array()
        .expect("tools array")
        .iter()
        .filter_map(|t| t["name"].as_str())
        .collect();
    assert_eq!(names, ["big", "fail", "refuse", "sleep", "touch"]);
    let hidden = by_id(&responses, 3);
    assert_eq!(hidden["result"]["isError"], true, "{hidden}");
    assert_eq!(text(hidden), "unknown tool 'echo'");
}

#[test]
fn a_session_without_envs_ignores_the_developers_own_config() {
    let home = std::env::temp_dir().join(format!("mcp-serve-core-home-{}", std::process::id()));
    let config_dir = home.join(".config").join("fleet-mcp");
    std::fs::create_dir_all(&config_dir).expect("create fake home");
    std::fs::write(
        config_dir.join("config.toml"),
        "[tools.demo]\nhide = [\"echo\"]\n",
    )
    .expect("write fake config");
    let reqs = with_handshake(vec![json!({"jsonrpc":"2.0","id":2,"method":"tools/list"})]);

    let responses = session_with(
        &demo_bin(),
        &reqs,
        &[("HOME", home.as_os_str()), (CONFIG_ENV, OsStr::new(""))],
    );
    let _ = std::fs::remove_dir_all(&home);

    let names: Vec<&str> = by_id(&responses, 2)["result"]["tools"]
        .as_array()
        .expect("tools array")
        .iter()
        .filter_map(|t| t["name"].as_str())
        .collect();
    assert!(names.contains(&"echo"), "ambient config leaked: {names:?}");
}

#[test]
fn a_handler_past_the_configured_deadline_times_out() {
    let reqs = with_handshake(vec![call(2, "sleep", json!({}))]);
    demo_bin();
    let started = Instant::now();
    let responses = Sandbox::new().run(&reqs, Some("[tools.demo]\ntimeout_secs = 1\n"));
    let elapsed = started.elapsed();

    let timed_out = by_id(&responses, 2);
    assert_eq!(timed_out["result"]["isError"], true, "{timed_out}");
    assert_eq!(text(timed_out), "timed out after 1s");
    assert!(
        elapsed < Duration::from_secs(8),
        "the session waited for the handler: {elapsed:?}"
    );
}

#[test]
fn a_client_cancellation_aborts_the_running_handler() {
    let sandbox = Sandbox::new();
    let marker = sandbox.0.join("touched");
    let reqs = with_handshake(vec![
        call(2, "touch", json!({"path": marker})),
        json!({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2}}),
    ]);
    sandbox.run(&reqs, None);

    assert!(
        !marker.exists(),
        "the canceled handler ran to completion and wrote {}",
        marker.display()
    );
}

#[test]
fn an_uncanceled_touch_call_writes_its_marker() {
    let sandbox = Sandbox::new();
    let marker = sandbox.0.join("touched");
    let reqs = with_handshake(vec![call(2, "touch", json!({"path": marker}))]);
    let responses = sandbox.run(&reqs, None);

    assert_eq!(text(by_id(&responses, 2)), "touched");
    assert!(marker.exists());
}

#[test]
fn a_session_past_its_exit_budget_kills_the_server_and_panics() {
    let sandbox = Sandbox::new();
    let marker = sandbox.0.join("touched");
    let reqs = with_handshake(vec![call(
        2,
        "touch",
        json!({"path": marker, "delay_ms": 3000}),
    )]);
    demo_bin();
    let started = Instant::now();
    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        sandbox.run_with_budget(&reqs, None, Duration::from_secs(1))
    }));
    let elapsed = started.elapsed();
    std::thread::sleep(Duration::from_secs(3));
    assert!(
        !marker.exists(),
        "the server outlived the helper and ran its handler to completion"
    );

    let payload = outcome.expect_err("the session must panic");
    let message = payload
        .downcast_ref::<String>()
        .unwrap_or_else(|| panic!("panic payload is not a formatted message"));
    assert!(
        message.contains("did not exit within 1 s of stdin closing"),
        "{message}"
    );
    assert!(
        elapsed < Duration::from_secs(4),
        "the helper waited for the server's own shutdown: {elapsed:?}"
    );
}

#[test]
fn output_past_the_cap_is_truncated_on_a_char_boundary_with_the_footer() {
    let reqs = with_handshake(vec![call(2, "big", json!({}))]);
    let responses = Sandbox::new().run(&reqs, Some("[tools.demo]\nmax_output_bytes = 1024\n"));

    let big = by_id(&responses, 2);
    assert_eq!(big["result"]["isError"], false, "{big}");
    let body = text(big)
        .strip_suffix(TRUNCATION_FOOTER)
        .unwrap_or_else(|| panic!("no truncation footer on {} bytes", text(big).len()));
    assert!(body.len() <= 1024, "{} bytes", body.len());
    assert!(body.len() > 1024 - 4, "{} bytes", body.len());
    assert!(body.chars().all(|c| c == '\u{20ac}'), "cut inside a char");
}
