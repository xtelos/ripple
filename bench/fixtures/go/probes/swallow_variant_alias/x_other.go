//go:build !linux

package sva

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

type A = C
