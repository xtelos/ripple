package sub

import "testing"

func TestSub(t *testing.T) {
	if Sub() != 3 {
		t.Error("sub is wrong")
	}
}
