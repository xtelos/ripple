package stg

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

	type (
		Named struct{ Base }
	)

	func (Named) M() {}
