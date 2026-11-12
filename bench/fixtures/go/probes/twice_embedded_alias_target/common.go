package tea

type Base struct{}

func (Base) M() {}

type Mid struct{ Base }

type Alt struct{}

func (Alt) M() {}

type T struct{ A }

func Use(t T) {
	t.M()
}
