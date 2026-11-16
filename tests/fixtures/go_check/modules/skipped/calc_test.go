package calc

import "testing"

func TestSkipped(t *testing.T) {
	t.Skip("not today")
}

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Fatal("1+2")
	}
}
