package a

type Base struct{}

func (Base) M() {}

type Box[T any] struct {
	Base
	v T
}

func (b Box[T]) Get() T { return b.v }

type Outer[T any] struct{ Box[T] }

type Inner[K comparable, V any] struct{ m map[K]V }

func (i Inner[K, V]) Len() int { return len(i.m) }

type Pair[K comparable, V any] struct{ Inner[K, V] }

func Use() {
	var b Box[int]
	b.M()
	_ = b.Get()
	var o Outer[string]
	o.M()
	_ = o.Get()
	var p Pair[string, int]
	_ = p.Len()
}
