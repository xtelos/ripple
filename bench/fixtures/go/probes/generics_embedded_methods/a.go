package a

type Inner[T any] struct{ v T }

func (i Inner[T]) Get() T { return i.v }

type Outer[T any] struct{ Inner[T] }

func (o *Outer[T]) Set(v T) { o.v = v }

type Named struct{ Outer[int] }

func Use() {
	var n Named
	n.Set(1)
	_ = n.Get()
	var o Outer[string]
	o.Set("a")
	_ = o.Get()
}
