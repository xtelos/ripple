package a

type T struct{}

func (T) Run() {}

type U struct{}

func (U) Run() {}

func pair() (T, U)         { return T{}, U{} }
func twice() (T, error)    { return T{}, nil }

func Named() (t T, u U) {
	t.Run()
	u.Run()
	t, u = pair()
	t.Run()
	return
}

func Partial() {
	a, b := pair()
	a.Run()
	b.Run()
	a, c := twice()
	a.Run()
	_ = c
}

func Range() {
	var ts []T
	for _, t := range ts {
		t.Run()
	}
	for _, u := range []U{{}} {
		func() { u.Run() }()
	}
	var m map[string]U
	for k, v := range m {
		_ = k
		v.Run()
	}
}

func TypeSwitch(x any) {
	switch v := x.(type) {
	case T:
		v.Run()
	case U:
		v.Run()
	case *T:
		v.Run()
	case int, string:
		_ = v
	default:
		_ = v
	}
}

func ParamShadow(t U) {
	t.Run()
	{
		t := T{}
		t.Run()
	}
	t.Run()
}

func Closure() {
	t := T{}
	f := func(t U) { t.Run() }
	f(U{})
	func() { t := U{}; t.Run() }()
	t.Run()
}
