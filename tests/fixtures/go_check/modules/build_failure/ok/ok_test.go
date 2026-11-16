package ok

import "testing"

func TestOne(t *testing.T) {
	if One() != 1 {
		t.Fatal("one")
	}
}
