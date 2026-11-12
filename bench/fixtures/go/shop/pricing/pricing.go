package pricing

import "example.com/shop/internal/money"

type Rule interface {
	Apply(c money.Cents) money.Cents
}

type Percent struct{ P int }

func (p Percent) Apply(c money.Cents) money.Cents { return money.Scale(c, p.P) }

type Flat struct{ Off money.Cents }

func (f *Flat) Apply(c money.Cents) money.Cents { return c - f.Off }

func Total(items []money.Cents, rules []Rule) money.Cents {
	sum := money.Sum(items)
	for _, r := range rules {
		sum = r.Apply(sum)
	}
	return sum
}

func Discount(c money.Cents) money.Cents {
	p := Percent{P: 10}
	f := &Flat{Off: 5}
	return f.Apply(p.Apply(c))
}

func Map[T, R any](xs []T, f func(T) R) []R {
	out := make([]R, 0, len(xs))
	for _, x := range xs {
		out = append(out, f(x))
	}
	return out
}

func Pair[K comparable, V any](k K, v V) map[K]V { return map[K]V{k: v} }
