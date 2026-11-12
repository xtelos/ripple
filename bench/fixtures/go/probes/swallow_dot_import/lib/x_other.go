//go:build !linux

package lib

func target() {}

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

var F = target
