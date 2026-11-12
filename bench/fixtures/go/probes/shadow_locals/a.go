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
		x.Run()
	}
	x.Run()
	if x := (U{}); true {
		x.Run()
	}
	x.Run()
	for _, x := range []U{{}} {
		x.Run()
	}
	x.Run()
	switch x := any(x).(type) {
	case U:
		x.Run()
	}
	x.Run()
	func() {
		x := U{}
		x.Run()
	}()
	x.Run()
}

func Param(helper func()) {
	helper()
}

func Later() {
	f := func() {}
	f()
	helper()
	var helper = func() {}
	helper()
}

func Multi() (u U) {
	x := T{}
	x, y := T{}, U{}
	x.Run()
	y.Run()
	u.Run()
	return
}

func ifElse() {
	x := T{}
	if x := (U{}); false {
	} else {
		x.Run()
	}
	x.Run()
}

func swInit() {
	x := T{}
	switch x := (U{}); {
	default:
		x.Run()
	}
	x.Run()
}

func label() {
	x := T{}
outer:
	for {
		x := U{}
		x.Run()
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
