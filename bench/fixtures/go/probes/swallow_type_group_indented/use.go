package stg

type Base struct{}

func (Base) M() {}

func Use(n Named) {
	n.M()
}
