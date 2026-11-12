package sto

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

func (T) M() {}
