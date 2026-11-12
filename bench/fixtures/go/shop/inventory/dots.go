package inventory

import (
	"example.com/shop/internal/money"
	. "example.com/shop/pricing"
)

// WithDot uses names of a dot-imported repo package without a qualifier.
func WithDot(c money.Cents) money.Cents {
	r := Percent{P: 3}
	return Discount(r.Apply(c))
}
