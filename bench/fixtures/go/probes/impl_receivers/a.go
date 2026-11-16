package a

// The method set of T has the value-receiver methods only; that of *T has both. An interface value can hold a *T, so a
// type whose pointer has every method may be what the value holds, though a T alone could not be assigned to it.

type Closer interface{ Close() error }

type PtrOnly struct{}

func (*PtrOnly) Close() error { return nil }

type ValOnly struct{}

func (ValOnly) Close() error { return nil }

type Both interface {
	Open() error
	Close() error
}

type Mixed struct{}

func (Mixed) Open() error   { return nil }
func (*Mixed) Close() error { return nil }

// an embedded value gets its pointer methods into *Outer only; *Outer is what the interface holds
type Inner struct{}

func (*Inner) Close() error { return nil }
func (Inner) Open() error   { return nil }

type Outer struct{ Inner }

// an embedded pointer gives both
type PtrOuter struct{ *Inner }

func Use(c Closer, b Both) {
	c.Close() // want: PtrOnly.Close, ValOnly.Close, Mixed.Close, Inner.Close
	b.Open()  // want: Mixed.Open, Inner.Open
	b.Close() // want: Mixed.Close, Inner.Close
}
