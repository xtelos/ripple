package inventory

import "example.com/shop/internal/money"

type ledger struct{ sum money.Cents }

func newLedger() *ledger { return &ledger{} }

func (l *ledger) Sum(xs []money.Cents) money.Cents { return l.sum }

// Shadow declares a local named like the imported package money: money.Sum is the ledger's method here.
func Shadow() money.Cents {
	money := newLedger()
	return money.Sum(nil)
}

func Unshadowed() money.Cents { return money.Sum(nil) }
