package a

type Base struct{}

func (Base) M() {}
func (Base) N() {}

type B struct{ Base }
type A = B
type C = A

func (C) M() {}

type W struct{ C }

func Use() {
	var b B
	b.M()
	b.N()
	var c C
	c.M()
	var w W
	w.M()
	w.N()
	w.C.M()
}
