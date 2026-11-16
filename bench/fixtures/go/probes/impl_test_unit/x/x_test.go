package x

import (
	"testing"

	lib "example.com/impl_test_unit"
	"example.com/impl_test_unit/internal/onlyx"
	"example.com/impl_test_unit/y"
)

func TestX(t *testing.T) {
	var d lib.Doer = y.Y{}
	_ = onlyx.OX{}
	d.Do() // want: Own.Do, internal/onlyx:OX.Do, y:Y.Do
}
