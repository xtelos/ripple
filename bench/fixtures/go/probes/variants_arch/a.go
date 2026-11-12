package a

type Base struct{}

func (Base) Size() int { return 0 }
func (Base) Align() int { return 0 }

type T struct{ Base }

func Use() {
	_ = width()
	var t T
	_ = t.Size()
	_ = t.Align()
}
