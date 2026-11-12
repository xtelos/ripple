package a

func helper() {}

type T struct{}

func (T) Run() {}

type U struct{}

func (U) Run() {}

func Order() {
	helper()
	x := T{}
	x.Run()
	{
		x.Run()
		x := U{}
		x.Run()
	}
	var y = func() { helper() }
	y()
	helper := func() {}
	helper()
	{
		z := x
		z.Run()
	}
}

func Self() {
	x := T{}
	{
		x := x
		x.Run()
	}
	var u U
	{
		u := u
		u.Run()
	}
}
