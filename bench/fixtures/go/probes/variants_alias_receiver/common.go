package a

type Base struct{}

func (Base) M() {}

type B struct{ Base }

type A = B

func Use() {
	var a A
	a.M()
	var b B
	b.M()
}
