package a

import (
	"testing"

	"example.com/impl_test_constrained/c"
)

// the sibling case: this file imports c, so c's type is in the test binary (every test file of a is compiled into it)
// and is a candidate for both calls; b's never is
func TestPlain(t *testing.T) {
	var r Runner = Real{}
	r.Run() // want: a:Real.Run, c:Linked.Run
	_ = c.Linked{}
}
