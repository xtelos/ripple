package calc

import "testing"

func TestTable(t *testing.T) {
	for _, name := range []string{"ok_case", "with space", "a/b", "bad", "bad2"} {
		t.Run(name, func(t *testing.T) {
			if len(name) >= 3 && name[:3] == "bad" {
				t.Errorf("%s is bad", name)
			}
		})
	}
}

func TestOther(t *testing.T) {
}
