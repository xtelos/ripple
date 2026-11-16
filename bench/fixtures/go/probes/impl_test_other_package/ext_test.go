package a_test

import (
	"testing"

	a "example.com/impl_test_other_package"
	"example.com/impl_test_other_package/b"
)

type Ext struct{}

func (Ext) Run() {}

func TestExt(t *testing.T) {
	var r a.Runner = Ext{}
	r.Run() // want: Real.Run, InA.Run, b:Prod.Run, _test:Ext.Run
	_ = b.Prod{}
}
