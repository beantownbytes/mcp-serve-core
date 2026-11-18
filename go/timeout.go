package mcpservecore

import (
	"context"
	"encoding/json"
	"math"
	"strconv"
	"time"
)

const (
	// DefaultTimeout is the configured timeout when none is set.
	DefaultTimeout = 120 * time.Second
	// MinTimeout is the smallest configured timeout.
	MinTimeout = time.Second
	// MaxTimeoutSeconds caps both the configured timeout and the per-call timeout a tool may
	// request for itself, before TimeoutMargin is added.
	MaxTimeoutSeconds = 2 * 60 * 60
)

// TimeoutMargin is added to a tool's own timeout_seconds so the tool reports its timeout
// before the server abandons it.
const TimeoutMargin = 30 * time.Second

const timeoutArgument = "timeout_seconds"

// EffectiveTimeout is the deadline for one call. configured is clamped to
// [MinTimeout, MaxTimeoutSeconds] first, as the Rust core's config does (a zero value becomes
// MinTimeout; use DefaultTimeout when nothing is configured). A tool that takes its own
// timeout_seconds is allowed to run that long, from the call argument or else schemaDefault
// (the schema's properties.timeout_seconds.default, nil when absent; any other value is ignored),
// clamped to MaxTimeoutSeconds, plus TimeoutMargin; the clamped configured value is the
// floor. The result lies in [MinTimeout, MaxTimeoutSeconds + TimeoutMargin].
//
// The argument counts only when it is a positive whole number; anything else (absent, zero,
// negative, fractional, a string, null) falls back to schemaDefault, and then to configured.
// A schemaDefault of 0 is not filtered and yields max(configured, TimeoutMargin).
func EffectiveTimeout(configured time.Duration, args map[string]any, schemaDefault any) time.Duration {
	configured = min(max(configured, MinTimeout), MaxTimeoutSeconds*time.Second)
	secs, ok := wholeSeconds(args[timeoutArgument])
	if !ok || secs == 0 {
		secs, ok = wholeSeconds(schemaDefault)
	}
	if !ok {
		return configured
	}
	secs = min(secs, MaxTimeoutSeconds)
	return max(configured, time.Duration(secs)*time.Second+TimeoutMargin)
}

// WithTimeout derives a context that expires after EffectiveTimeout.
func WithTimeout(ctx context.Context, configured time.Duration, args map[string]any, schemaDefault any) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, EffectiveTimeout(configured, args, schemaDefault))
}

// wholeSeconds reads v as a non-negative whole number of seconds. It accepts int, int32,
// int64, uint, uint32 and uint64, and the float64 and json.Number values that encoding/json
// produces.
func wholeSeconds(v any) (uint64, bool) {
	switch n := v.(type) {
	case int:
		return fromSigned(int64(n))
	case int32:
		return fromSigned(int64(n))
	case int64:
		return fromSigned(n)
	case uint:
		return uint64(n), true
	case uint32:
		return uint64(n), true
	case uint64:
		return n, true
	case float64:
		if n < 0 || n != math.Trunc(n) || n >= math.MaxUint64 {
			return 0, false
		}
		return uint64(n), true
	case json.Number:
		u, err := strconv.ParseUint(string(n), 10, 64)
		return u, err == nil
	default:
		return 0, false
	}
}

func fromSigned(n int64) (uint64, bool) {
	if n < 0 {
		return 0, false
	}
	return uint64(n), true
}
