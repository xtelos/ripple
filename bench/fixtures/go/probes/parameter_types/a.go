package a

type T struct{}

func (T) M() {}

func Use(xs ...T) {
	xs[0].M()
}

func Maps(m map[string][]T, c <-chan T, fn func() T, arr [3]*T, f func(T)) {
	m["a"][0].M()
	(<-c).M()
	fn().M()
	arr[0].M()
	f(T{})
}
