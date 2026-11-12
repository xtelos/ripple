package swi

type S struct{ A int }

func broken() *S {
	p := new(S{
		A: 1,
	})
	return p
}

	func (T) M() {}
