package a

// What a signature or an embedded type means can depend on the build too: an alias that names another type on each
// platform, and an embedded type whose method has another signature on each. Nothing about such a type is proven.

type Adder interface{ Add(Num) }

// right on linux only, where Num is int
type Linux struct{}

func (Linux) Add(int) {}

// written with Num, which is not one type
type Written struct{}

func (Written) Add(Num) {}

type Session interface {
	Open()
	Close()
}

type Outer struct{ E }

func (Outer) Close() {}

// the siblings that are proven: a signature with no alias in it, and an embedded type that is the same on every platform
type Plain interface{ Take(int) }

type Fixed struct{}

func (Fixed) Take(int) {}

type Session2 interface {
	Hold()
	Drop()
}

type Stable struct{}

func (Stable) Hold() {}

type Outer2 struct{ Stable }

func (Outer2) Drop() {}

func Use(a Adder, s Session, p Plain, s2 Session2) {
	a.Add(0)  // want: none
	s.Open()  // want: none
	s.Close() // want: none
	p.Take(0) // want: Fixed.Take
	s2.Hold() // want: Stable.Hold
	s2.Drop() // want: Outer2.Drop
}
