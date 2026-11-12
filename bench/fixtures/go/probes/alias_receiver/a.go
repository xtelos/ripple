package a

type Base struct{}

func (Base) M()    {}
func (*Base) P()   {}
func (Base) Only() {}

// A method declared through an alias receiver is B's own method, and it shadows Base.M.
type B struct{ Base }
type A = B

func (A) M() {}

func UseAlias() {
	var b B
	b.M()
	var a A
	a.M()
	b.Only()
	b.P()
	pa := &a
	pa.M()
}

// A defined type over a struct drops B's declared methods and keeps the promoted ones.
type D B

func UseDefined() {
	var d D
	d.M()
}
