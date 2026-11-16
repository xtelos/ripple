package a

import "testing"

func TestSame(t *testing.T) {
	if One() != 1 {
		t.Fatal("a")
	}
}

func TestOnlyA(t *testing.T) {
}
