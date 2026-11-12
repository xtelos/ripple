package swc

type S struct{ A int }

func broken() *S {
	return new(S{
		A: 1,
	})
}

/* M shadows the promoted Base.M. */ func (T) M() {}
