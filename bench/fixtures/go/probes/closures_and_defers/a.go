package a

type T struct{}

func (T) M() {}

func helper() {}

func Use() {
	t := T{}
	func() { t.M(); helper() }()
	defer func() { t.M() }()
	go func() { helper() }()
	f := func() T { return t }
	f().M()
	for i := 0; i < 2; i++ {
		defer t.M()
	}
	g := func(x T) { x.M() }
	g(t)
}
