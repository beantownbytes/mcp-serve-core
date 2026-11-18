// Package mcpservecore holds the Go side of the native-MCP serving core. It is a set of
// SDK-independent helpers that keep a Go tool server byte-compatible with the Rust core's
// per-call deadline (EffectiveTimeout, WithTimeout), output cap and truncation footer
// (ClampOutputBytes, CapText), error labels (PolicyError, ErrorText) and fail-closed access
// annotation check (CheckAnnotations). It does not serve MCP itself and has no helper for the
// timeout, canceled, unknown-tool or panic result texts.
package mcpservecore
