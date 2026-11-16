package a

import (
	"testing"

	"example.com/impl_test_method_other_package/b"
)

// b is built into this test binary as it is without its tests: Half has no Run here, Whole has
func TestA(t *testing.T) {
	var r Runner = Real{}
	r.Run() // want: a:Real.Run, b:Whole.Run
	_, _ = b.Half{}, b.Whole{}
}
