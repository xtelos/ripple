package a

// A defined type whose underlying type is a pointer has an empty method set, its pointee's or any promoted one (only a field
// selector looks through it). An alias of a pointer is the pointer type itself, and embedded it promotes the methods.

type I interface {
	M() int
	N()
}

type E struct{}

func (E) M() int { return 0 }

type F struct{}

func (F) N() {}

// S does not satisfy I (its own M has another result), and neither E nor F alone does
type S struct {
	E
	F
}

func (S) M() string { return "" }

type P *S

type PP P

type J interface {
	G() int
	H()
}

type E2 struct{}

func (E2) G() int { return 0 }

type F2 struct{}

func (F2) H() {}

type PE2 = *E2

// Q embeds an alias of *E2: G is promoted, and Q satisfies J
type Q struct {
	PE2
	F2
}

type K interface {
	M4() int
	N4()
}

type E4 struct{}

func (E4) M4() int { return 0 }

type F4 struct{}

func (F4) N4() {}

// S4 embeds by pointer and has its own M4 of another result: it does not satisfy K
type S4 struct {
	*E4
	*F4
}

func (S4) M4() string { return "" }

type P4 *S4

// a defined type of an interface type is an interface: it has no methods of its own to be a candidate
type Shower interface{ Show() }

type Wrapped Shower

type Sh struct{}

func (Sh) Show() {}

func Use(i I, j J, k K, w Wrapped, s Shower) {
	i.M()  // want: none
	i.N()  // want: none
	j.G()  // want: E2.G
	j.H()  // want: F2.H
	k.M4() // want: none
	k.N4() // want: none
	w.Show() // want: Sh.Show
	s.Show() // want: Sh.Show
}
