package b

import (
	"testing"

	"example.com/impl_test_method_other_package/a"
)

// the method a _test.go file declares is in b's own test binary and in no other (a's, above, has no Half.Run)
func (Half) Run() {}

func TestB(t *testing.T) {
	var r a.Runner = Half{}
	r.Run() // want: a:Real.Run, b:Whole.Run, b:Half.Run
}
