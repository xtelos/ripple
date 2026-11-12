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
	b.M() // real: A.M (declared with alias receiver)
	var a A
	a.M() // real: A.M
	b.Only()
}

// Defined type over a struct: drops B's declared methods, keeps promoted ones.
type D B

func UseDefined() {
	var d D
	d.M() // real: Base.M (B's own M dropped)
}
