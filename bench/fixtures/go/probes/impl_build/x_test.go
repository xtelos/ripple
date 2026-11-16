package a

import "testing"

// a type only the test build has: not a candidate for a call of a build without it
type InTest struct{}

func (InTest) Start() {}
func (InTest) Stop()  {}

func TestUse(t *testing.T) {
	var r Runner = InTest{}
	r.Start() // want: Always.Start, InTest.Start, _test:Ext.Start
}
