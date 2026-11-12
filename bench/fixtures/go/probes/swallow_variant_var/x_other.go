//go:build !linux

package swvv

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

var X Other
