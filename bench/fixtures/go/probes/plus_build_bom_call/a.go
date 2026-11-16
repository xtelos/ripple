package a

type E struct{}

func (E) M() {}

type T struct{ E }

func Use(t T) {
	t.M()
}
