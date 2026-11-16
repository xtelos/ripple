package a

// A generic type has a method set only for each instantiation, so its methods are not followed; neither is a generic
// interface (its methods depend on the type argument), a call through a type parameter, or a method promoted from an
// instance of a generic type. A plain type with the same method is still proven.

type IntGetter interface{ Get() int }

type Plain struct{}

func (Plain) Get() int { return 1 }

type Box[T any] struct{ v T }

func (b Box[T]) Get() T { return b.v }

// Get comes from Box[int]
type Wrap struct{ Box[int] }

type Getter[T any] interface{ Get() T }

type Holder[T any] struct{}

func (Holder[T]) Get() T { var zero T; return zero }

func ThroughInterface(g IntGetter) {
	g.Get() // want: Plain.Get
}

func ThroughGeneric(g Getter[int]) {
	g.Get() // want: none
}

func ThroughParameter[T IntGetter](t T) {
	t.Get() // want: none
}

func ThroughInstance[T any](g Getter[T]) {
	g.Get() // want: none
}
