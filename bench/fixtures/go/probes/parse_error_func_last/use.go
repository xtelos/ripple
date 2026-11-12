package d

type Base struct{}

func (Base) M() {}

type T struct{ Base }

func Use() {
	var t T
	t.M()
	t.N()
	helper()
	_ = g()
}
