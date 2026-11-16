package calc

import "testing"

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Fatal("1+2")
	}
}

func TestAddZero(t *testing.T) {
	if Add(0, 0) != 0 {
		t.Fatal("0+0")
	}
}
