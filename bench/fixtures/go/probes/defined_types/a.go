package a

type Base struct{}

func (Base) M() {}

type B struct{ Base }

func (B) Own() {}

type D B
type PD *B

type F func()

func (f F) Call() { f() }

type I interface{ M() }
type J I

func Use(i I, j J) {
	var d D
	d.M()
	F(func() {}).Call()
	i.M()
	j.M()
	var f F = func() {}
	f.Call()
	f()
	var b B
	b.Own()
	var pd PD = &b
	_ = pd
}
