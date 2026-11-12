//go:build !linux

package svt

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

type T struct{ Other }
