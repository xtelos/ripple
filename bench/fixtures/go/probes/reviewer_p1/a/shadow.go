package a

type T struct{}

func (T) Run() {}

type U struct{}

func (U) Run() {}

func helper() {}

func Shadow() {
	x := T{}
	{
		x := U{}
		x.Run() // U.Run
	}
	x.Run() // T.Run
	if x := (U{}); true {
		x.Run() // U.Run
	}
	x.Run() // T.Run
	for _, x := range []U{{}} {
		x.Run() // U.Run
	}
	x.Run() // T.Run
	switch x := any(x).(type) {
	case U:
		x.Run() // U.Run
	}
	x.Run() // T.Run
	func() {
		x := U{}
		x.Run() // U.Run
	}()
	x.Run() // T.Run
}

func Param(helper func()) {
	helper() // param: unresolved
}

func Later() {
	f := func() {}
	f()
	helper()
	var helper = func() {}
	helper() // local: unresolved
}

func Multi() (u U) {
	x := T{}
	x, y := T{}, U{}
	x.Run() // T.Run
	y.Run() // U.Run
	u.Run() // U.Run
	return
}

func Goto() {
	x := T{}
	{
		var x U
		x.Run() // U.Run
	}
	x.Run()
}

func ifElse() {
	x := T{}
	if x := (U{}); false {
	} else {
		x.Run() // U.Run
	}
	x.Run()
}

func swInit() {
	x := T{}
	switch x := (U{}); {
	default:
		x.Run() // U.Run
	}
	x.Run()
}

func label() {
	x := T{}
outer:
	for {
		x := U{}
		x.Run() // U.Run
		break outer
	}
	x.Run()
}

func sel() {
	x := T{}
	select {
	default:
		x := U{}
		x.Run()
	}
	x.Run()
}
