package att

type Base struct{}

func (Base) M() {}

type B struct{ Base }

type C struct{ Base }

func (A) M() {}

func Use() {
	var b B
	b.M()
}

func UseC() {
	var c C
	c.M()
}
