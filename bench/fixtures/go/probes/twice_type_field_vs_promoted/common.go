package ttf

type Base struct{}

func (Base) M() {}

func Use(h Holder) {
	h.M()
}
