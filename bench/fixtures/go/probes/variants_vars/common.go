package v

type A struct{}

func (A) Do() {}

type B struct{}

func (B) Do() {}

func shared() *A { return &A{} }

func Use() {
	Y.Do()
	Z.Do()
	W.Do()
}
