package e

type A struct{}
type B struct{}

type S struct {
	A
	B
}

func Use() {
	var s S
	s.M()
}
