package a

// A method found on an embedded type that is built everywhere still belongs to a type that is not: Linux (extra_linux.go)
// has A through E1 and B through E3, with no method of its own, so on linux the interface has an implementer and on
// windows it has none (neither E1 nor E3 has both), and a call built wherever it is cannot be said to reach E1.A.
// Wide is built everywhere, so E2.C is reached through it.

type LinuxOnly interface {
	A()
	B()
}

type E1 struct{}

func (E1) A() {}

type E3 struct{}

func (E3) B() {}

type Everywhere interface {
	C()
	D()
}

type E2 struct{}

func (E2) C() {}

type Wide struct{ E2 }

func (Wide) D() {}

func Use(l LinuxOnly, e Everywhere) {
	l.A() // want: none
	l.B() // want: none
	e.C() // want: E2.C
	e.D() // want: Wide.D
}
