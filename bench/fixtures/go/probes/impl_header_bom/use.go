//go:build !linux

package a

func Use(i I) {
	i.M() // want: none
}
