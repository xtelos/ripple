package sha

type Base struct{}

func (Base) M() {}

type B struct{ Base }

func (A) M() {}

func Use(b B) {
	b.M()
}
