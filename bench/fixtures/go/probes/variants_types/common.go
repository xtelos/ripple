package c

type Base struct{}

func (Base) M() {}

func Use() {
	var t T
	t.M()
}
