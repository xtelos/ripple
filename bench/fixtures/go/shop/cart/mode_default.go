//go:build !fast

package cart

import "example.com/shop/pricing"

var Strategy = pricing.Percent{P: 2}
