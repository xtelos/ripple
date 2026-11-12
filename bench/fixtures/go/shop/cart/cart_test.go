package cart_test

import (
	"fmt"
	"testing"

	"example.com/shop/cart"
)

func TestRun(t *testing.T) {
	cart.Run()
	t.Run("checkout", func(t *testing.T) { _ = cart.Checkout(cart.New()) })
}

func ExampleNew() {
	fmt.Println(cart.New() != nil)
	// Output: true
}

func BenchmarkAdd(b *testing.B) {
	c := cart.New()
	for i := 0; i < b.N; i++ {
		c.Add(1)
	}
}
