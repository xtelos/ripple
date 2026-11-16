package calc

import "testing"

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Fatal("1+2")
	}
}

func TestBad(t *testing.T) {
	if Add(2, 2) != 5 {
		t.Errorf("want 5, got %d", Add(2, 2))
	}
}
