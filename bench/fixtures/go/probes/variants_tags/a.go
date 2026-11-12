package a

type Base struct{}

func (Base) M() {}

type T struct{ Base }

func Use() {
	_ = mode()
	var t T
	t.M()
}
