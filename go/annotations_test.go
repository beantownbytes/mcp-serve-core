package mcpservecore

import "testing"

func ptr(b bool) *bool { return &b }

func TestCheckAnnotations(t *testing.T) {
	tests := []struct {
		name  string
		tools []ToolHints
		want  string
	}{
		{"empty registry passes", nil, ""},
		{"read-only passes", []ToolHints{{Name: "a", ReadOnly: ptr(true)}}, ""},
		{"write passes", []ToolHints{{Name: "a", ReadOnly: ptr(false), Destructive: ptr(false)}}, ""},
		{"destructive write passes (also the shape mcp-go NewTool seeds, so a forgotten option is not caught)", []ToolHints{{Name: "a", ReadOnly: ptr(false), Destructive: ptr(true)}}, ""},
		{"no hints at all fails", []ToolHints{{Name: "a"}}, "tools without an access classification: a"},
		{"not read-only with no destructive hint fails", []ToolHints{{Name: "a", ReadOnly: ptr(false)}}, "tools without an access classification: a"},
		{"destructive hint alone fails", []ToolHints{{Name: "a", Destructive: ptr(true)}}, "tools without an access classification: a"},
		{"read-only with a destructive hint passes (the shape mcp-go NewTool plus a read-only option yields)", []ToolHints{{Name: "a", ReadOnly: ptr(true), Destructive: ptr(true)}}, ""},
		{"read-only and explicitly not destructive passes", []ToolHints{{Name: "a", ReadOnly: ptr(true), Destructive: ptr(false)}}, ""},
		{"duplicate names fail, each named once", []ToolHints{
			{Name: "a", ReadOnly: ptr(true)}, {Name: "a", ReadOnly: ptr(true)}, {Name: "a", ReadOnly: ptr(true)},
		}, "duplicate tool names: a"},
		{"every failure is named in one error", []ToolHints{
			{Name: "first"}, {Name: "second"},
			{Name: "dup", ReadOnly: ptr(true)}, {Name: "dup", ReadOnly: ptr(true)},
		}, "tools without an access classification: first, second; duplicate tool names: dup"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := CheckAnnotations(tt.tools)
			switch {
			case tt.want == "" && err != nil:
				t.Fatalf("unexpected error: %v", err)
			case tt.want != "" && err == nil:
				t.Fatalf("want error %q, got nil", tt.want)
			case tt.want != "" && err.Error() != tt.want:
				t.Fatalf("error = %q, want %q", err.Error(), tt.want)
			}
		})
	}
}
