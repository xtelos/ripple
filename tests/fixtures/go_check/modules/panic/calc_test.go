package calc

import "testing"

func TestAfter(t *testing.T) {
}

func TestPanics(t *testing.T) {
	var xs []int
	_ = xs[Add(1, 2)]
}
