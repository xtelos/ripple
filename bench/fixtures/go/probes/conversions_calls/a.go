package a

type Num int

func (Num) Double() Num { return 0 }

type Handler func()

func (h Handler) Serve() { h() }

type Str string

func (Str) Len() int { return 0 }

type Ptr *Num

type T struct{}

func (T) M() {}

type I interface{ M() }

func Use(f func(), b []byte, p *T) {
	_ = Num(3).Double()
	_ = Num(3)
	Handler(f).Serve()
	Handler(f)()
	_ = Str("x").Len()
	_ = string(b)
	_ = []byte("x")
	(*T)(p).M()
	I(p).M()
	_ = (func())(f)
	(func())(f)()
	_ = interface{ M() }(T{})
	var h Handler = f
	h.Serve()
	_ = Ptr(nil)
	_ = float64(Num(1))
}
