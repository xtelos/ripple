package a

type A struct{}

func (A) M() {}

type B struct{}

func (B) M() {}

type T struct{ A }

func (T) N() {}

func Use() {
	type T struct{ B }
	var t T
	t.M()
}

func Use2() {
	var t T
	t.M()
	t.N()
	{
		type A struct{}
		var a A
		_ = a
	}
}

func Use3() {
	var t T
	t.M()
	type T struct{ B }
	var u T
	u.M()
}
