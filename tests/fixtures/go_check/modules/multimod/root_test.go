package multimod

import "testing"

func TestRoot(t *testing.T) {
	if Root() != 1 {
		t.Fatal("root")
	}
}
