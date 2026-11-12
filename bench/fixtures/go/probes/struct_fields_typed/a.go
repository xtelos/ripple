package a

type Leaf struct{}

func (Leaf) M()  {}
func (*Leaf) P() {}

type Mid struct {
	leaf    Leaf
	ptr     *Leaf
	leaves  []Leaf
	byName  map[string]*Leaf
	arr     [2]Leaf
}

type Root struct {
	mid  Mid
	pm   *Mid
	anon struct{ l Leaf }
}

func Use(r Root) {
	r.mid.leaf.M()
	r.mid.ptr.M()
	r.mid.leaves[0].M()
	r.mid.byName["a"].P()
	r.mid.arr[1].M()
	r.pm.leaf.M()
	r.anon.l.M()
	struct{ l Leaf }{}.l.M()
	var x struct{ l *Leaf }
	x.l.P()
}
