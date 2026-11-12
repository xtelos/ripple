package a

type Runner interface{ Run() }

type Impl struct{}

func (Impl) Run() {}

type Base struct{}

func (*Base) Ptr() {}

type S struct {
	*Base
	Runner
}

type Wrapper struct{ Runner }

func (Wrapper) Run() {}

type Both interface {
	Runner
	Stop()
}

type Embed struct{ Both }

func Use(s S, w Wrapper, e Embed) {
	s.Ptr()
	s.Run()
	w.Run()
	w.Runner.Run()
	e.Run()
	e.Stop()
	var i Impl
	i.Run()
	var r Runner = i
	r.Run()
}
