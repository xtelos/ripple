package pricing

import "example.com/shop/internal/money"

// Go 1.26 new(expr): tree-sitter-go 0.25.0 does not parse it, so this file must be listed as unparsable.
func Ptr(xs []money.Cents) *money.Cents { return new(money.Cents(money.Sum(xs))) }

func Twice(c money.Cents) money.Cents { return Discount(Discount(c)) }
