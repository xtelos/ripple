package a

type T struct{}

func (T) M() T   { return T{} }
func (*T) P() *T { return nil }

type I interface{ Do() }

func NewT() *T                  { return &T{} }
func Pair() (*T, error)         { return &T{}, nil }
func Iface() I                  { return nil }
func Closure() func() T         { return func() T { return T{} } }

func Use() {
	NewT().M().M()
	NewT().P().P()
	t, err := Pair()
	_ = err
	t.M()
	Iface().Do()
	Closure()().M()
	x := NewT()
	y := x.M()
	y.M()
	z := *x
	z.M()
	w := &z
	w.P()
}
