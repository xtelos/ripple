//go:build !linux

package tgt

type Box[T any] struct {
	v T
	n int
}
