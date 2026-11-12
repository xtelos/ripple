package d

type Base struct{}

func (Base) M() {}

type T struct{ Base }

func Use() {
	var t T
	t.M() // real: T.M (declared in broken.go)
}
