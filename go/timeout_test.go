package mcpservecore

import (
	"context"
	"encoding/json"
	"math"
	"testing"
	"time"
)

func TestEffectiveTimeout(t *testing.T) {
	const configured = 120 * time.Second
	tests := []struct {
		name          string
		args          map[string]any
		schemaDefault any
		want          time.Duration
	}{
		{"no argument and no default", nil, nil, configured},
		{"argument clears the tool's own wait plus margin", map[string]any{"timeout_seconds": 1800}, 300, 1830 * time.Second},
		{"argument as float64 from encoding/json", map[string]any{"timeout_seconds": float64(1800)}, nil, 1830 * time.Second},
		{"argument as json.Number", map[string]any{"timeout_seconds": json.Number("1800")}, nil, 1830 * time.Second},
		{"schema default used when the argument is absent", nil, 300, 330 * time.Second},
		{"schema default as float64", nil, float64(300), 330 * time.Second},
		{"configured is the floor", map[string]any{"timeout_seconds": 5}, nil, configured},
		{"an absurd argument is clamped", map[string]any{"timeout_seconds": uint64(math.MaxUint64)}, nil, (MaxTimeoutSeconds * time.Second) + TimeoutMargin},
		{"a float64 of 2^64 falls back to the schema default", map[string]any{"timeout_seconds": float64(1 << 64)}, 300, 330 * time.Second},
		{"a float64 of 2^64 with no default falls back to configured", map[string]any{"timeout_seconds": float64(1 << 64)}, nil, configured},
		{"json.Number of 2^64-1 is clamped like Rust", map[string]any{"timeout_seconds": json.Number("18446744073709551615")}, 300, MaxTimeoutSeconds*time.Second + TimeoutMargin},
		{"json.Number of 2^64 falls back to the schema default", map[string]any{"timeout_seconds": json.Number("18446744073709551616")}, 300, 330 * time.Second},
		{"json.Number with a fraction part falls back like Rust", map[string]any{"timeout_seconds": json.Number("1800.0")}, 300, 330 * time.Second},
		{"json.Number in exponent form falls back like Rust", map[string]any{"timeout_seconds": json.Number("1e3")}, 300, 330 * time.Second},
		{"fractional json.Number falls back to the schema default", map[string]any{"timeout_seconds": json.Number("10.5")}, 300, 330 * time.Second},
		{"zero argument falls back to the schema default", map[string]any{"timeout_seconds": 0}, 300, 330 * time.Second},
		{"negative argument falls back to the schema default", map[string]any{"timeout_seconds": -5}, 300, 330 * time.Second},
		{"fractional argument falls back to the schema default", map[string]any{"timeout_seconds": 10.5}, 300, 330 * time.Second},
		{"string argument falls back to the schema default", map[string]any{"timeout_seconds": "1800"}, 300, 330 * time.Second},
		{"null argument falls back to the schema default", map[string]any{"timeout_seconds": nil}, 300, 330 * time.Second},
		{"unusable argument and no default", map[string]any{"timeout_seconds": "soon"}, nil, configured},
		{"unusable schema default is ignored", nil, "300", configured},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := EffectiveTimeout(configured, tt.args, tt.schemaDefault); got != tt.want {
				t.Fatalf("EffectiveTimeout = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestEffectiveTimeoutClampsTheConfiguredValue(t *testing.T) {
	tests := []struct {
		name       string
		configured time.Duration
		want       time.Duration
	}{
		{"zero is raised to the floor", 0, MinTimeout},
		{"negative is raised to the floor", -time.Second, MinTimeout},
		{"within range is kept", DefaultTimeout, DefaultTimeout},
		{"above the ceiling is lowered", 3 * MaxTimeoutSeconds * time.Second, MaxTimeoutSeconds * time.Second},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := EffectiveTimeout(tt.configured, nil, nil); got != tt.want {
				t.Fatalf("EffectiveTimeout(%v) = %v, want %v", tt.configured, got, tt.want)
			}
		})
	}
}

func TestEffectiveTimeoutSchemaDefaultZeroIsNotFiltered(t *testing.T) {
	if got := EffectiveTimeout(10*time.Second, nil, 0); got != TimeoutMargin {
		t.Fatalf("EffectiveTimeout = %v, want %v", got, TimeoutMargin)
	}
}

func TestWithTimeoutBoundsTheContext(t *testing.T) {
	ctx, cancel := WithTimeout(context.Background(), 2*time.Second, map[string]any{"timeout_seconds": 100}, nil)
	defer cancel()
	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("context has no deadline")
	}
	remaining := time.Until(deadline)
	if remaining > 130*time.Second || remaining < 120*time.Second {
		t.Fatalf("remaining = %v, want about 130s", remaining)
	}
}
