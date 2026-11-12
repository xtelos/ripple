package c

type Base struct{}

func (Base) M() {}

type Mid struct{ Base }

type Outer struct{ Mid }

func Use() {
	var o Outer
	o.M()
	var m Mid
	m.M()
}
