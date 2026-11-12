package c

type A struct{}

func (A) Do() {}

type B struct{}

func (B) Do() {}

func Use() {
	m := Make()
	m.Do()
	Make().Do()
	p, err := Pair()
	_ = err
	p.Do()
}
