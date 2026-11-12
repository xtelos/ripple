package sup

type T struct{}

func (T) M() {}

func F() {}

func Use(t T) {
	F()
	t.M()
	_ = len("x")
}
