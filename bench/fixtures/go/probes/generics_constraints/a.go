package a

type Stringer interface{ String() string }

type S struct{}

func (S) String() string { return "" }

func Call[T Stringer](x T) string { return x.String() }

func Inline[T interface{ Close() }](x T) { x.Close() }

type C struct{}

func (C) Close() {}

func Union[T ~int | ~string](x T) T { return x }

func Use() {
	_ = Call(S{})
	_ = Call[S](S{})
	Inline(C{})
	_ = Union(3)
	var s S
	_ = s.String()
}
