package cbs

type S struct{ A int }

func broken() *S {
	s := "}" + `{` + string('}') // {
	return new(S{
		A: len(s),
	})
}

func (T) M() {}
