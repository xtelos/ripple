package a

// The interface a value is held as is what the source writes for it, in every form ripple already types a receiver:
// a parameter, a field, an element, a result, an assertion, an embedded interface, a named or an aliased interface, and
// a call that is a go or defer statement or sits in a package-level initialiser. A local type of the same name hides it.

type Runner interface{ Run() int }

type Fast struct{}

func (Fast) Run() int { return 1 }

type Slow struct{}

func (*Slow) Run() int { return 2 }

type Other interface{ Run(n int) int }

type Never struct{}

func (Never) Run(n int) int { return n }

type Named Runner
type Alias = Runner

type Holder struct{ Runner }

type Box struct {
	r    Runner
	list []Runner
	byID map[string]Runner
	ch   chan Runner
}

var global Runner = Fast{}

var _ = global.Run() // want: Fast.Run, Slow.Run

func make1() Runner { return Fast{} }

func Forms(b Box, h Holder, n Named, al Alias, any1 any) {
	b.r.Run()    // want: Fast.Run, Slow.Run
	b.list[0].Run() // want: Fast.Run, Slow.Run
	for _, r := range b.list {
		r.Run() // want: Fast.Run, Slow.Run
	}
	b.byID["x"].Run() // want: Fast.Run, Slow.Run
	(<-b.ch).Run()    // want: Fast.Run, Slow.Run
	make1().Run()     // want: Fast.Run, Slow.Run
	h.Run()           // want: Fast.Run, Slow.Run
	n.Run()           // want: Fast.Run, Slow.Run
	al.Run()          // want: Fast.Run, Slow.Run
	global.Run()      // want: Fast.Run, Slow.Run
	any1.(Runner).Run() // want: Fast.Run, Slow.Run
	if r, ok := any1.(Runner); ok {
		r.Run() // want: Fast.Run, Slow.Run
	}
	switch x := any1.(type) {
	case Runner:
		x.Run() // want: Fast.Run, Slow.Run
	}
	go b.r.Run()    // want: Fast.Run, Slow.Run
	defer b.r.Run() // want: Fast.Run, Slow.Run
	func() {
		b.r.Run() // want: Fast.Run, Slow.Run
	}()
}

func Shadowed(r Runner) {
	{
		type Runner interface{ Run(x, y int) int }
		var s Runner
		s.Run(1, 2) // want: none
	}
	r.Run() // want: Fast.Run, Slow.Run
}

func Expression(r Runner) {
	_ = Runner.Run
	Runner.Run(r) // want: none
}

func Other1(o Other) {
	o.Run(1) // want: Never.Run
}
