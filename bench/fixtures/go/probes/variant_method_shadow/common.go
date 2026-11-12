package c

type Base struct{}

func (Base) M() {}
func (Base) K() {}

type T struct{ Base }

func UseMethodSet() {
	var t T
	t.M()
	t.K()
}
