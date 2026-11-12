package a

type Base struct{}

func (Base) M() {}
func (Base) N() {}

type T struct {
	Base
	M func()
}

type U struct {
	Base
	N int
}

func Use() {
	var t T
	t.M()
	t.N()
	t.Base.M()
	var u U
	u.M()
	_ = u.N
}
