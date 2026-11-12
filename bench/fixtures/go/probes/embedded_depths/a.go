package a

type L3 struct{}

func (L3) Deep() {}
func (L3) Same() {}

type L2 struct{ L3 }

func (L2) Mid() {}

type L1 struct{ L2 }

func (L1) Same() {}

type X1 struct{}

func (X1) Same() {}

// Top.Same would be ambiguous (L1.Same and X1.Same are both one level down), and is never selected.
type Top struct {
	L1
	*X1
}

type Mixed struct {
	L2
	X1
}

func Use() {
	var l1 L1
	l1.Deep()
	l1.Mid()
	l1.Same()
	var l2 L2
	l2.Same()
	var m Mixed
	m.Same()
	m.Deep()
	var t Top
	t.Deep()
	t.Mid()
	t.L1.Same()
	t.X1.Same()
}
