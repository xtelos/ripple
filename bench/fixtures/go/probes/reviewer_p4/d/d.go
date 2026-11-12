package d

type Base struct{}

func (Base) M() {}

type T struct{ Base }

func g() uint64 { return 1 }

func Broken() *uint64 { return new(g()) }

func (T) M() {}

func Use() {
	var t T
	t.M() // T.M
}

var Counter = new(uint64(g()))

func (T) N() {}

func Use2() {
	var t T
	t.N()
	helper()
}

func helper() {}

func BrokenMethodLike(t T) *bool { t.M(); return new(bool(false)) }

type V struct{ Base }

func (V) M() {}

func Use3() { var v V; v.M() }
