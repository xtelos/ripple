//go:build !linux

package svg

type S struct{ A int }

func helper() *S {
	return new(S{
		A: 1,
	})
}

func Map[T any](xs []T) { helper() }
