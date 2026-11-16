package a

import "strings"

type Inner struct{}

func (Inner) M() {}

type S struct {
	F Inner
	Inner2
	B strings.Builder
}

type Inner2 struct{ G Inner }

func (Inner2) N() {}

type P *S

func Use(p P) {
	p.F.M()
	p.G.M()
	p.B.Len()
	(*p).N()
	q := p
	q.F.M()
}
