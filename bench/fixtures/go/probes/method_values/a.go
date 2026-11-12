package a

type T struct{}

func (T) M()  {}
func (*T) P() {}

type Base struct{}

func (Base) B() {}

type S struct{ Base }

type I interface{ M() }

func Use(i I) {
	t := T{}
	f := t.M
	f()
	g := (*T).P
	g(&t)
	h := T.M
	h(t)
	T.M(t)
	(*T).P(&t)
	s := S{}
	k := s.B
	k()
	S.B(s)
	I.M(i)
	m := i.M
	m()
	defer t.M()
	go t.P()
}
