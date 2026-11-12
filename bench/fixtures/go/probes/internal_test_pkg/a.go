package a

type T struct{}

func (T) M() {}

func Run() {
	var t T
	t.M()
}
