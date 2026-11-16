package a

type I interface {
	M()
	N()
}

type E struct{}

func (E) M() {}

type T struct {
	E
	X
}

func (T) N() {}
