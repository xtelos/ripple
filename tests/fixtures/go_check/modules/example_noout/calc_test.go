package calc

import "testing"

func TestAdd(t *testing.T) {
	if Add(1, 2) != 3 {
		t.Fatal("1+2")
	}
}

func ExampleAdd() {
	_ = Add(1, 2)
}

func ExampleAdd_withOutput() {
	_ = Add(1, 2)
	// Output:
}

func ExampleAdd_unordered() {
	_ = Add(1, 2)
	// Unordered output:
}
