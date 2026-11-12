package scr

type S struct{ A int }

func broken() *S {
	return new(S{
		A: 1,
	})
}

	func (T) M() {}
