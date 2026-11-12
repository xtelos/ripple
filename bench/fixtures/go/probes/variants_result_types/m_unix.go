//go:build !windows

package c

func Make() *A { return &A{} }

func Pair() (*A, error) { return &A{}, nil }
