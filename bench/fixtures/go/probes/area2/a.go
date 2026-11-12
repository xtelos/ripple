package a2

import (
	"example.com/a2/ctxpkg"
	"example.com/a2/errors"
	"example.com/a2/internal/hid"
	"example.com/a2/v2lib/v2"
)

type Thing struct{}

func (Thing) M()      {}
func (*Thing) P()     {}
func (Thing) N() Thing { return Thing{} }

type Other struct{}

func (Other) M() {}

type I interface{ M() }

type J interface {
	I
	K()
}

type Box[T any] struct{ v T }

func (b Box[T]) Get() T     { return b.v }
func (b *Box[T]) Set(v T)   { b.v = v }
func MakeBox[T any]() Box[T] { return Box[T]{} }

type W struct{ Box[int] }

type G[T any] struct{ Box[T] }

type E struct{ I }

type Holder[Thing any] struct{ v Thing }

func (h Holder[Thing]) V() Thing { return h.v }

func Call[T interface{ M() }](t T) {
	t.M()
}

type Num interface{ ~int | ~float64 }

func Sum[T Num](xs ...T) T {
	var s T
	for _, x := range xs {
		s += x
	}
	return s
}

func min(a, b int) int { return a }

func clear() {}

func Generics() {
	var b Box[string]
	b.Get()
	b.Set("x")
	MakeBox[int]().Get()
	var w W
	w.Get()
	var g G[int]
	g.Get()
	var h Holder[Thing]
	h.V().M()
	Call(Thing{})
	Call[Other](Other{})
	Sum(1, 2)
}

func MethodValues() {
	var t Thing
	f := t.M
	f()
	g := Thing.M
	g(t)
	Thing.M(t)
	(*Thing).P(&t)
	(*Thing).M(&t)
	defer t.M()
	go t.P()
	defer f()
	func() { t.M() }()
}

func Interfaces() {
	var i I = Thing{}
	i.M()
	var j J
	j.M()
	e := E{Thing{}}
	e.M()
	I(Thing{}).M()
	var x any = Thing{}
	x.(Thing).M()
	x.(*Thing).P()
	if v, ok := x.(Other); ok {
		v.M()
	}
}

func SameLine() {
	var t Thing
	var o Other
	t.M(); o.M()
	t.N().M()
	min(min(1, 2), 3)
	clear()
}

func Shadows(errors Thing) {
	errors.M()
}

func Packages() {
	errors.Is(errors.New("a"), nil)
	context.Background().Done()
	hid.F()
	lib.Make().Do()
}

func Anon() {
	s := struct{ f func() }{f: clear}
	s.f()
	func() { clear() }()
}

func RangeFunc(seq func(func(Thing) bool)) {
	for x := range seq {
		x.M()
	}
}

func Labels() {
	var t Thing
L:
	for {
		t.M()
		break L
	}
	goto M
M:
	t.P()
}
