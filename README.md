# mcp-serve-core

Shared native-MCP serving core for tool servers: each server supplies its handlers and this core supplies the serving behavior. Library only: no shipped binaries.

- `rust/`: crate `mcp-serve-core`, a stdio MCP server runtime on `rmcp` for in-process tool handlers.
- `go/`: Go module `github.com/beantownbytes/mcp-serve-core/go`, helpers that keep a Go tool server's deadline arithmetic, labels, truncation footer and annotation check byte-compatible with the Rust core. It has no helper for the `timed out after <N>s`, `canceled`, `unknown tool` or `tool handler panicked` texts; a Go server renders those itself.

Gate: `make check` (fmt-check, lint, test in both sub-projects). CI runs it on every PR and on main.

## What the core owns

A tool author supplies handlers; the core supplies the serving behavior so it is identical across tools:

- Result shaping: every outcome is an MCP result, never a protocol error.
- A byte cap on returned text, cut on a character boundary, with a footer.
- A per-call deadline, extendable by a tool's own `timeout_seconds`.
- Per-server config (a `[tools.<name>]` section keyed by the name passed to `ServeConfig::load`) can hide tools, override descriptions and instructions, and clamp the timeout and output cap.
- Access annotations (`Access`) that fail closed.

### Wire contract

| Outcome | `isError` | Text |
|---|---|---|
| Unknown or hidden tool | true | `unknown tool '<name>'` (handler never runs) |
| Deadline hit | true | `timed out after <N>s` (no body, no footer; task aborted) |
| Client canceled (`notifications/cancelled`) | true | `canceled` (no body, no footer; task aborted; rmcp drops the response, so the client sees none) |
| Handler panicked | true | `tool handler panicked` |
| `Ok(text)` | false | the text |
| `ToolError::Policy(m)` | true | `policy refusal (exit 3): <m>` |
| `ToolError::Tool(m)` / `ToolError::Config(m)` | true | `exit 2: <m>` |

A body longer than the cap is cut at the largest character boundary at or below the cap and gains `\n[output truncated at the configured byte cap]`. The cap bounds the returned text only (label and footer excluded). A truncated success stays `isError=false`; error bodies are right-trimmed after the cut.

### Config

The file named by the environment variable the consumer passes to `ServeConfig::load` when it is set and non-empty; otherwise `$XDG_CONFIG_HOME/fleet-mcp/config.toml`, else `$HOME/.config/fleet-mcp/config.toml` (an empty `XDG_CONFIG_HOME` or `HOME` counts as unset). The directory name `fleet-mcp` is a fixed convention shared with the tools that consume this crate and does not follow the crate name. A missing default file, neither `XDG_CONFIG_HOME` nor `HOME` set, or a file without a `[tools.<name>]` section for the consumer, means defaults; a missing explicitly named file is an error. Unknown keys anywhere in the file are an error.

```toml
[tools.websearch]
hide = ["web_fetch"]
instructions = "One line shown to the model."
timeout_secs = 120        # default 120, clamped to [1, 7200]
max_output_bytes = 262144 # default 256 KiB, clamped to [1024, 64 MiB]

[tools.websearch.descriptions]
web_search = "Shorter description."
```

Naming a tool in `hide` or `descriptions` that the catalog does not contain fails at startup.

### Deadline

The deadline is the configured timeout. A call that carries a positive whole `timeout_seconds` argument (or, failing that, whose schema declares a whole-number default for it; a default of 0 is not filtered and yields 30 s, floored at the configured timeout) may run for the requested value (clamped to 7200 s) plus a 30 s margin, and the configured timeout is the floor. Range: 1 s to 7230 s.

## Rust

### Depend on it

From crates.io:

```toml
[dependencies]
mcp-serve-core = "0.1"
```

Or by git, pinned to an exact commit:

```toml
[dependencies]
mcp-serve-core = { git = "https://github.com/beantownbytes/mcp-serve-core.git", rev = "<sha>" }
```

MSRV is 1.88 (forced by `rmcp` 3.10.0, edition 2024). `cargo +1.88.0 check --all-features --locked` passed when this was written; CI does not enforce it. A consumer on an older toolchain must raise its `rust-version`.

`rmcp` is pinned `=3.10.0` and re-exported as `mcp_serve_core::rmcp`, so consumers need no `rmcp` dependency of their own. `rmcp` itself depends on `rmcp-macros` with a caret requirement, so the macro crate resolves to the newest compatible release in the lockfile.

### Use it

```rust
// `schema` is the tool's JSON Schema as a `JsonObject`; see rust/examples/demo.rs for a complete program.
use mcp_serve_core::config::ServeConfig;
use mcp_serve_core::rmcp::model::JsonObject;
use mcp_serve_core::server::{ServerInfo, serve_stdio};
use mcp_serve_core::spec::{Access, ToolError, ToolSpec};

let spec = ToolSpec::new("echo", "Return the `text` argument.", schema, |args: JsonObject| async move {
    args.get("text")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .ok_or_else(|| ToolError::Tool("missing `text`".into()))
})
.with_access(Access::ReadOnly);

let config = ServeConfig::load("mytool", "MYTOOL_MCP_CONFIG")?;
serve_stdio(ServerInfo::new("mytool", "1.0.0", "Instructions."), vec![spec], config).await?;
```

`rust/examples/demo.rs` is a complete runnable server and the usage reference; the stdio tests drive it.

Access classification maps to MCP annotations: `ReadOnly` emits `readOnlyHint: true` only (destructiveness is left to the client default, so `readOnlyHint` alone does not assert non-destructive); `Write` emits `readOnlyHint: false, destructiveHint: false`; `DestructiveWrite` emits `readOnlyHint: false, destructiveHint: true`. A spec with no `Access` emits no annotations, which a harness treats as a destructive write and prompts for.

### Test it

The `testing` cargo feature (enable it on a dev-dependency) exports `session`, `session_with`, `session_with_budget`, `handshake`, `by_id` and `assert_fail_closed`. `session_with_budget` takes an exit budget: if the server has not exited that long after stdin closes, it kills and reaps the server, then panics; `session_with` uses a 30 s budget. Call `assert_fail_closed(&specs)` from a unit test so an unclassified tool or a duplicate name fails in the consumer's CI. `session_with` isolates only the default config path; a tool that names its config through an environment variable must set or blank that variable in `envs`.

### Behavior to know

- A timed-out or canceled call (client `notifications/cancelled`) aborts the handler task at its next await point. It does not roll back side effects, and it cannot stop work already moved to `spawn_blocking`. A timed-out or canceled mutating tool may have run. For a canceled call rmcp 3.10.0 drops the response, so the client sees none.
- Handler concurrency is unbounded: every `tools/call` spawns a task.
- The byte cap bounds the returned text, not the handler's memory: the handler has already built the whole body when the cap applies.
- A panic payload is never returned to the client, but the default panic hook still prints it to stderr.
- `serve_stdio` rejects duplicate tool names and config naming unknown tools before any I/O.

## Go

### Depend on it

```
go get github.com/beantownbytes/mcp-serve-core/go@<sha>
```

Pin a commit, as the Rust git snippet does: `@<sha>` records the resolved pseudo-version (`v0.0.0-<timestamp>-<sha>`) in the consumer's `go.mod` and `go.sum`. `@main` drifts as main advances and is only for a throwaway trial.

### Helpers

The package has no SDK dependency, so a consumer keeps its own `mcp-go` pin.

| Function | Rust counterpart | Behavior |
|---|---|---|
| `EffectiveTimeout(configured, args, schemaDefault)` | `ToolConfig` clamp + `deadline::effective_timeout` | `configured` clamped to [1 s, 7200 s] (a zero value becomes 1 s, so pass `DefaultTimeout` when nothing is configured), then the same table: positive whole `timeout_seconds` or schema default, clamped to 7200 s, plus 30 s; the clamped `configured` is the floor |
| `WithTimeout(ctx, configured, args, schemaDefault)` | | `context.WithTimeout` over `EffectiveTimeout` |
| `CapText(body, capBytes)`, `ClampOutputBytes(n)` | `result` cut + footer, `ToolConfig` clamp | Rune-boundary cut with `TruncationFooter`; `ClampOutputBytes` limits a configured cap to [1024 B, 64 MiB] (`CapText` applies exactly the cap it is given) |
| `PolicyError`, `ErrorText(err, capBytes)` | `ToolError::Policy` / `Tool` / `Config` | `policy refusal (exit 3): ` for a (possibly wrapped) `*PolicyError`, else `exit 2: `; capped, then trailing Unicode white space trimmed, then `TruncationFooter` when it cut (label and footer excluded from the cap; `err` must be non-nil) |
| `CheckAnnotations([]ToolHints)` | `testing::assert_fail_closed` | Fails on unclassified tools and duplicate names (like the Rust check; `destructiveHint` is ignored when `readOnlyHint` is true). `mcp.NewTool` seeds `readOnlyHint: false, destructiveHint: true`, which is the same as a deliberate destructive write, so a forgotten option on a `NewTool` tool is not caught; feed the check hints from the consumer's own classification (nil for unclassified) to get the fail-closed behavior |

`ToolHints` maps one-to-one to `mark3labs/mcp-go` `mcp.Tool.Annotations` (`ReadOnlyHint *bool`, `DestructiveHint *bool`, verified against v1.5.0):

```go
import mcpservecore "github.com/beantownbytes/mcp-serve-core/go"

// Only fail-closed if the hints come from the consumer's own classification, not NewTool defaults.
hints := make([]mcpservecore.ToolHints, 0, len(tools))
for _, tool := range tools {
	hints = append(hints, mcpservecore.ToolHints{
		Name: tool.Name, ReadOnly: tool.Annotations.ReadOnlyHint, Destructive: tool.Annotations.DestructiveHint,
	})
}
if err := mcpservecore.CheckAnnotations(hints); err != nil {
	t.Fatal(err)
}
```

A `json.Number` (from a decoder using `UseNumber`) is parsed as an unsigned integer exactly as the Rust core does, so `1800.0`, `1e3` and values of 2^64 or more fall back, and `18446744073709551615` is clamped to a 7230 s deadline. A Go argument decoded without `UseNumber` arrives as `float64`, where `1800.0` is indistinguishable from `1800` and is accepted (Rust rejects it), and a value whose `float64` form is 2^64 or more is rejected, including `18446744073709551615`, which rounds up. A Go `uint64` argument is clamped. `go.mod` declares `go 1.27`, so a consumer needs that toolchain or newer.

## Development

```
make bootstrap   # toolchains (Rust 1.97.1, Go 1.27.3)
make check       # fmt-check, lint, test in rust/ and go/
```

The Rust crate version in `rust/Cargo.toml` is the release number (`make version` prints it) and is edited by hand. Publishing is `cargo publish` from `rust/`; the Go module lives in `go/`, so its releases will be tagged `go/vX.Y.Z` (none exist yet; pin a commit until then).

## License

MIT. See `LICENSE`.
