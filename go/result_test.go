package mcpservecore

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestCapText(t *testing.T) {
	const footer = "\n[output truncated at the configured byte cap]"
	tests := []struct {
		name string
		body string
		cap  int
		want string
	}{
		{"within the cap", "hello", 16, "hello"},
		{"exactly at the cap is not cut", "hello", 5, "hello"},
		{"one byte over is cut", "hello!", 5, "hello" + footer},
		{"a multibyte rune straddling the cap is dropped whole", "ab\u20ac", 3, "ab" + footer},
		{"a multibyte rune ending exactly at the cap is kept", "ab\u20ac", 5, "ab\u20ac"},
		{"cap zero cuts everything", "x", 0, footer},
		{"negative cap is treated as zero", "x", -3, footer},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := CapText(tt.body, tt.cap); got != tt.want {
				t.Fatalf("CapText(%q, %d) = %q, want %q", tt.body, tt.cap, got, tt.want)
			}
		})
	}
}

func TestErrorText(t *testing.T) {
	const footer = "\n[output truncated at the configured byte cap]"
	tests := []struct {
		name string
		err  error
		cap  int
		want string
	}{
		{"policy refusal", &PolicyError{Message: "not allowed"}, 64, "policy refusal (exit 3): not allowed"},
		{"wrapped policy refusal", fmt.Errorf("outer: %w", &PolicyError{Message: "no"}), 64, "policy refusal (exit 3): outer: no"},
		{"tool error", errors.New("bad flag"), 64, "exit 2: bad flag"},
		{"trailing whitespace is trimmed", errors.New("bad flag\n\n"), 64, "exit 2: bad flag"},
		{"trailing Unicode white space is trimmed like Rust trim_end", errors.New("x\v\f\u00a0\u2028 "), 64, "exit 2: x"},
		{"a truncated body is trimmed before the footer", errors.New("abc   def"), 6, "exit 2: abc" + footer},
		{"a truncated policy refusal keeps its label", &PolicyError{Message: strings.Repeat("x", 10)}, 4, "policy refusal (exit 3): xxxx" + footer},
		{"a multibyte rune straddling the cap is dropped whole", errors.New("ab\u20ac"), 3, "exit 2: ab" + footer},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ErrorText(tt.err, tt.cap); got != tt.want {
				t.Fatalf("ErrorText = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestClampOutputBytes(t *testing.T) {
	tests := []struct{ in, want int }{
		{0, MinOutputBytes},
		{-1, MinOutputBytes},
		{MinOutputBytes - 1, MinOutputBytes},
		{DefaultMaxOutputBytes, DefaultMaxOutputBytes},
		{MaxOutputBytesCeiling + 1, MaxOutputBytesCeiling},
	}
	for _, tt := range tests {
		if got := ClampOutputBytes(tt.in); got != tt.want {
			t.Fatalf("ClampOutputBytes(%d) = %d, want %d", tt.in, got, tt.want)
		}
	}
}
