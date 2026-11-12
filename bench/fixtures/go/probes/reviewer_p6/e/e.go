package e

type A struct{}
type B struct{}
type S struct {
	A
	B
}

func Use() {
	var s S
	s.M()
}

func Local() {
	type T struct{ S }
	var t T
	t.M()
}

type T struct{}

func (T) M() {}
