package b

import "testing"

// only b's own test binary has this type
type Fake struct{}

func (Fake) Run() {}

type Runner interface{ Run() }

func TestFake(t *testing.T) {
	var r Runner = Fake{}
	r.Run() // want: b:Prod.Run, b:Fake.Run
}
