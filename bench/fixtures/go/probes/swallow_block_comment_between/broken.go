package sbc

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

/* one */ /* two */ func /* three */ (T) /* four */ M() {}
