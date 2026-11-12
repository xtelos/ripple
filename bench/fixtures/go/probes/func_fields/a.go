package a

type S struct {
	f   func()
	fs  []func()
	m   map[string]func()
	ret func() func()
}

type Base struct{}

func (Base) Run() {}

type T struct {
	Base
	Run func()
}

func mk() func() { return func() {} }

func Use(s S, t T) {
	s.f()
	s.fs[0]()
	s.m["a"]()
	s.ret()()
	mk()()
	t.Run()
	t.Base.Run()
	var local func()
	local()
	fns := map[string]func(){"a": mk()}
	fns["a"]()
	for _, f := range s.fs {
		f()
	}
}
