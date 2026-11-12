package c

type A struct{}

func (A) Do() {}

type B struct{}

func (B) Do() {}

func Use() {
	f := X
	f().Do()
	g := f()
	g.Do()
}
