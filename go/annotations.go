package mcpservecore

import (
	"errors"
	"slices"
	"strings"
)

// ToolHints is the access classification of one registered tool, as the MCP annotations
// carry it. The fields map to the SDK's tool annotation pointers (for mark3labs/mcp-go,
// Tool.Annotations.ReadOnlyHint and Tool.Annotations.DestructiveHint).
type ToolHints struct {
	Name        string
	ReadOnly    *bool
	Destructive *bool
}

// CheckAnnotations verifies a tool registry fails closed: every tool is classified and names
// are unique. A tool is classified when its
// read-only hint is true, or when the hint is explicitly false and the destructive hint is set;
// an unclassified tool (no read-only hint, or false without a destructive hint) is treated by a
// harness as a destructive write. A tool whose hints are (false, true) is indistinguishable from
// a deliberate destructive write, so the unclassified case only fires for hints that a
// consumer supplied from its own classification, or from an SDK that leaves them unset;
// mcp-go's NewTool seeds (false, true) and so never trips it. It returns nil for an empty
// registry, and otherwise one error naming every failure.
func CheckAnnotations(tools []ToolHints) error {
	seen := make(map[string]bool, len(tools))
	var unclassified, duplicates []string
	for _, t := range tools {
		if !classified(t) {
			unclassified = append(unclassified, t.Name)
		}
		if seen[t.Name] && !slices.Contains(duplicates, t.Name) {
			duplicates = append(duplicates, t.Name)
		}
		seen[t.Name] = true
	}
	var failures []string
	if len(unclassified) > 0 {
		failures = append(failures, "tools without an access classification: "+strings.Join(unclassified, ", "))
	}
	if len(duplicates) > 0 {
		failures = append(failures, "duplicate tool names: "+strings.Join(duplicates, ", "))
	}
	if len(failures) == 0 {
		return nil
	}
	return errors.New(strings.Join(failures, "; "))
}

func classified(t ToolHints) bool {
	if t.ReadOnly == nil {
		return false
	}
	return *t.ReadOnly || t.Destructive != nil
}
