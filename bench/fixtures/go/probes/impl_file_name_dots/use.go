//go:build !windows

package a

func Use2(i I) {
	i.N() // want: none
}
