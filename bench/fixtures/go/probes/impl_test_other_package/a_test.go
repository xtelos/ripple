package a

import "testing"

type InA struct{}

func (InA) Run() {}

func TestUse(t *testing.T) {
	var r Runner = InA{}
	r.Run() // want: Real.Run, InA.Run, b:Prod.Run, _test:Ext.Run
}
