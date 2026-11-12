package a

type A struct{}

func (A) M()     {}
func (A) OnlyA() {}

type B struct{}

func (B) M() {}

type S struct {
	A
	B
}

type T struct {
	A
	B
}

func (T) M() {}

func Use() {
	var s S
	s.A.M()
	s.B.M()
	s.OnlyA()
	var t T
	t.M()
}
