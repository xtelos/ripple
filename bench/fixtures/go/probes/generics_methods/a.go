package a

type Box[T any] struct{ v T }

func (b Box[T]) Get() T   { return b.v }
func (b *Box[T]) Put(v T) { b.v = v }

type Pair[K comparable, V any] struct {
	k K
	v V
}

func (p Pair[K, V]) Key() K { return p.k }

func Make[T any](v T) Box[T] { return Box[T]{v} }

type Num int

func (Num) Double() Num { return 0 }

func Use() {
	b := Make(1)
	_ = b.Get()
	b.Put(2)
	Make[string]("x").Get()
	var p Pair[string, int]
	_ = p.Key()
	var bn Box[Num]
	bn.Get().Double()
	bn.Put(Num(1))
}
