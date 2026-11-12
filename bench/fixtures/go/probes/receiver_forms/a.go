package a

type T struct{}

func (T) M() {}

func (_ T) N() { T{}.M() }

func (t T) Shadowed(T int) {
	_ = T
	t.M()
}

func (t *T) Self() {
	t.M()
	t = nil
	(*t).M()
}

func (t T) Param(t2 T) { t2.M() }
