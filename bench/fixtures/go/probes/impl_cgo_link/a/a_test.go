//go:build !cgo

package a

import "testing"

// a build with cgo has c.go and so links b, and has no file of this one: no build has this call and b's T
func TestA(t *testing.T) {
	var i I = Own{}
	i.M() // want: a:Own.M
}
