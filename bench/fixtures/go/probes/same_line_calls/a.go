package sl

type A struct{}

func (A) M() {}

type B struct{}

func (B) M() {}

func (a A) Self() A { return a }

func (b B) Other() A { return A{} }

func one(A) {}

func Use(a A, b B) {
	a.M(); b.M()
	a.Self().M()
	b.Other().Self().M()
	one(a.Self().Self())
	b.M(); a.M(); b.M()
}
