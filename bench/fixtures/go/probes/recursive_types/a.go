package a

type Node struct {
	next *Node
	kids []*Node
	m    map[string]*Node
}

func (n *Node) Walk() {}

type A struct{ *B }
type B struct{ *A }

func (A) FromA() {}
func (B) FromB() {}

func Use(n *Node) {
	n.next.Walk()
	n.kids[0].Walk()
	n.m["x"].next.next.Walk()
	var a A
	a.FromA()
	a.FromB()
	a.B.FromB()
	a.B.A.FromA()
	var b B
	b.FromA()
}
