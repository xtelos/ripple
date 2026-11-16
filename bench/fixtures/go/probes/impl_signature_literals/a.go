package a

// An interface literal other than `interface{}` is a type of its own method set. Ripple does not follow one, so a method
// that takes it is never proven to be the interface's: not Same, whose literal is the interface's (the compiler says it
// implements it), and above all not Other, whose literal has another method. `interface{}` is `any`, and is followed.

type Lit interface{ F(interface{ A() }) }

type Same struct{}

func (Same) F(interface{ A() }) {}

type Other struct{}

func (Other) F(interface{ B() }) {}

type Empty interface{ G(any) }

type Spelled struct{}

func (Spelled) G(interface{}) {}

func Use(l Lit, e Empty) {
	l.F(nil) // want: none
	e.G(nil) // want: Spelled.G
}
