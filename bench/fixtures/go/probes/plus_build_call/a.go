package a

type E struct{}

func (E) M() {}

type T struct{ E }

type V struct{ E }

type W struct{ E }

type X struct{ E }

func Use(t T, v V, w W, x X) {
	t.M()
	v.M()
	w.M()
	x.M()
}
