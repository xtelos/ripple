package b

import "testing"

func TestSame(t *testing.T) {
	if Two() != 3 {
		t.Error("b is wrong")
	}
}

func TestOnlyB(t *testing.T) {
}
