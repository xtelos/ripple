package a

// `func (A) Run()` is a method of whatever A names: B on linux, C on the other platforms. Neither type is proven to have
// it, so neither is a candidate.

type Runner interface{ Run() }

type B struct{}

type C struct{}

func (A) Run() {}

// the sibling: an alias that names one type on every platform has its methods as that type's, under the alias's name

type Walker interface{ Walk() }

type D = E

type E struct{}

func (D) Walk() {}

func Use(r Runner, w Walker) {
	r.Run()  // want: none
	w.Walk() // want: D.Walk
}
