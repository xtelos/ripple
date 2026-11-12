package a

func Map[T, U any](v T, f func(T) U) U { return f(v) }

func Id[T any](v T) T { return v }

func Apply[T any](f func(T) T, v T) T { return f(v) }

func Use() {
	f := Map[int, string]
	_ = f(1, nil)
	_ = Map(1, func(i int) string { return "" })
	_ = Apply(Id[int], 1)
	g := Id[string]
	_ = g("x")
	fns := []func(int) int{Id[int]}
	_ = fns[0](1)
	_ = Id(2)
}
