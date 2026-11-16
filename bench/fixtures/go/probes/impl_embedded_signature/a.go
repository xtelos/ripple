package a

// A method a type gets from an embedded interface has the signature that interface gives it, and the type implements
// another interface only when that is the signature the other wants. The method itself has no source here, so the
// edges are to the type's own Z.

type J interface{ M(int) }

type T struct{ J }

func (T) Z() {}

type WantsString interface {
	M(string)
	Z()
}

type WantsInt interface {
	M(int)
	Z()
}

func Use(s WantsString, i WantsInt) {
	s.Z() // want: none
	i.Z() // want: T.Z
	i.M(0) // want: none
}
