package cp

type NeedsCgo interface {
	A()
	B()
}

type Always interface {
	C()
	D()
}

type E1 struct{}

func (E1) A() {}

type E3 struct{}

func (E3) B() {}

type E2 struct{}

func (E2) C() {}

type E4 struct{}

func (E4) D() {}

// the sibling of Native (c.go): the same shape in a file every build has
type Pure struct {
	E2
	E4
}

func Use(n NeedsCgo, a Always) {
	n.A() // want: none
	n.B() // want: none
	a.C() // want: E2.C
	a.D() // want: E4.D
}
