package a

type L1 struct{ L2 }
type L2 struct{ L3 }
type L3 struct{}

func (L3) Deep() {}
func (L2) Mid()  {}

type X1 struct{}

func (X1) Same() {}

type X2 struct{}

func (X2) Same() {}

type Two struct {
	X1
	*X2
}

type Depths struct {
	X1
	L1
}

func (L1) Same() {}

func Embedding() {
	var l L1
	l.Deep()
	l.Mid()
	var t Two
	_ = t
	var d Depths
	_ = d
}
