//go:build !neverset

package a

import "testing"

func TestConstrained(t *testing.T) {
	var r Runner = Real{}
	r.Run() // want: a:Real.Run, c:Linked.Run
}
