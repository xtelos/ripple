package a

type T struct{}

func (T) M() {}

func F[T interface{ M() }](x T) {
	x.M()
}

type Box[T any] struct{ v T }

func (b Box[T]) Use() {
	var t T
	_ = t
}

func Use() {
	F(T{})
	var t T
	t.M()
}
