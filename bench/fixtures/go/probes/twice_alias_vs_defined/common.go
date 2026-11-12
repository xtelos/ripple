package tad

type Base struct{}

func (Base) M() {}

type Other struct{}

func (Other) M() {}

func (A) N() {}

func Use(a A) {
	a.M()
}

func UseN(a A) {
	a.N()
}
