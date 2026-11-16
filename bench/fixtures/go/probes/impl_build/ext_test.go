package a_test

import (
	"testing"

	a "example.com/impl_build"
)

type Ext struct{}

func (Ext) Start() {}
func (Ext) Stop()  {}

func TestExt(t *testing.T) {
	var r a.Runner = Ext{}
	r.Stop() // want: Always.Stop, InTest.Stop, _test:Ext.Stop
}
