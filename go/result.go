package mcpservecore

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	// ExitPolicyRefusal is the exit code the policy-refusal label reports.
	ExitPolicyRefusal = 3
	// ExitToolError is the exit code the tool-error label reports.
	ExitToolError = 2
)

const (
	// DefaultMaxOutputBytes is the byte cap when none is configured.
	DefaultMaxOutputBytes = 256 * 1024
	// MinOutputBytes is the smallest cap a configuration may set.
	MinOutputBytes = 1024
	// MaxOutputBytesCeiling is the largest cap a configuration may set.
	MaxOutputBytesCeiling = 64 * 1024 * 1024
)

// TruncationFooter is appended to a body cut at the byte cap.
const TruncationFooter = "\n[output truncated at the configured byte cap]"

// PolicyError is a refusal by policy rather than a failure of the tool. ErrorText labels it
// "policy refusal (exit 3): ".
type PolicyError struct {
	Message string
}

func (e *PolicyError) Error() string { return e.Message }

// ClampOutputBytes limits a configured byte cap to [MinOutputBytes, MaxOutputBytesCeiling],
// the range the Rust core enforces. CapText itself applies exactly the cap it is given.
func ClampOutputBytes(capBytes int) int {
	return min(max(capBytes, MinOutputBytes), MaxOutputBytesCeiling)
}

// CapText cuts body at the largest rune boundary at or below capBytes and appends
// TruncationFooter when it cut anything. A body within the cap is returned unchanged, and a
// negative cap is treated as zero. The cap is applied as given; see ClampOutputBytes.
func CapText(body string, capBytes int) string {
	cut, truncated := cutAt(body, capBytes)
	if truncated {
		return cut + TruncationFooter
	}
	return cut
}

// ErrorText is the text of a handler failure (a policy refusal or a tool error); err must not
// be nil. The timeout, unknown-tool and panic texts are not label-formatted, so do not pass
// them here. A *PolicyError (also when wrapped) is labeled "policy refusal (exit 3): ", any
// other error "exit 2: ". The message is cut at capBytes and trailing Unicode white space is
// trimmed after the cut, then TruncationFooter is appended when it cut anything. The label and
// footer are not counted against the cap.
func ErrorText(err error, capBytes int) string {
	label := fmt.Sprintf("exit %d: ", ExitToolError)
	var policy *PolicyError
	if errors.As(err, &policy) {
		label = fmt.Sprintf("policy refusal (exit %d): ", ExitPolicyRefusal)
	}
	cut, truncated := cutAt(err.Error(), capBytes)
	out := label + strings.TrimRightFunc(cut, unicode.IsSpace)
	if truncated {
		out += TruncationFooter
	}
	return out
}

func cutAt(body string, capBytes int) (string, bool) {
	capBytes = max(capBytes, 0)
	if len(body) <= capBytes {
		return body, false
	}
	end := capBytes
	for end > 0 && !utf8.RuneStart(body[end]) {
		end--
	}
	return body[:end], true
}
