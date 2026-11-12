//go:build !linux

package tgf

func Map[T, U any](xs []T, f func(T) U) []U {
	helperO()
	return nil
}

func helperO() {}
