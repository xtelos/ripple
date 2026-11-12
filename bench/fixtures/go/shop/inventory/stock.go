package inventory

import (
	"bytes"
	"reflect"
	"strings"
	"sync"
	"time"

	"example.com/shop/internal/money"
	"example.com/shop/pricing"
)

type Item struct {
	SKU   string
	Price money.Cents
}

func (it Item) Cost() money.Cents { return it.Price }

// Items is a named slice: ranging over one types its elements.
type Items []Item

func (xs Items) Sum() money.Cents {
	var total money.Cents
	for _, x := range xs {
		total += x.Cost()
	}
	return total
}

func parse() (it Item, err error) { return Item{}, nil }

func Parsed() money.Cents {
	it, err := parse()
	if err != nil {
		return 0
	}
	return it.Cost()
}

func pick() (*pricing.Percent, error) { return &pricing.Percent{P: 1}, nil }

// Reprice redeclares its parameter r with :=, which reuses it: r stays a pricing.Rule, so r.Apply is an interface call.
func Reprice(r pricing.Rule, c money.Cents) money.Cents {
	r, err := pick()
	if err != nil {
		return c
	}
	return r.Apply(c)
}

type Stack[T any] struct{ items []T }

func (s *Stack[T]) Push(x T) { s.items = append(s.items, x) }

func (s *Stack[T]) Pop() T {
	last := s.items[len(s.items)-1]
	s.items = s.items[:len(s.items)-1]
	return last
}

func UseStack() money.Cents {
	s := &Stack[Item]{}
	s.Push(Item{})
	it := s.Pop() // T is a type parameter of Pop: it.Cost is not proven
	return it.Cost()
}

func SumAll[T interface{ Cost() money.Cents }](xs []T) money.Cents {
	var total money.Cents
	for _, x := range xs {
		total += x.Cost() // x's type is a type parameter
	}
	return total
}

func Costs(xs Items) []money.Cents {
	return pricing.Map[Item, money.Cents](xs, Item.Cost)
}

// Base, Mid and Top: a method promoted from depth 2 is not proven when a type embedded from outside the repo sits at depth 1.
type Base struct{}

func (Base) Reset() {}

type Mid struct{ Base }

type Top struct {
	Mid
	*bytes.Buffer
}

func ResetTop(t *Top) { t.Reset() }

func ResetMid(m *Mid) { m.Reset() }

type Stream struct{ *bytes.Buffer }

func Size(s *Stream) int { return s.Len() }

type Auditor struct{ pricing.Rule }

func Audit(a *Auditor, c money.Cents) money.Cents { return a.Apply(c) }

var Hook func(int)

var handlers = map[string]func(int){"x": func(int) {}}

func makeHandler() func(int) { return func(int) {} }

func Dispatch(name string) {
	Hook(1)
	handlers[name](2)
	makeHandler()(3)
}

func Dynamic(v any) {
	reflect.ValueOf(v).MethodByName("Cost").Call(nil)
}

func Describe(v any) money.Cents {
	switch x := v.(type) {
	case *Item:
		return x.Cost()
	case Item, money.Cents:
		_ = x // two types in one case: x keeps the type of v (any)
	}
	return 0
}

func Concat() string {
	var sb strings.Builder
	sb.WriteString("x")
	var mu sync.Mutex
	mu.Lock()
	defer mu.Unlock()
	d := 2 * time.Second
	_ = d.Seconds()
	return sb.String()
}

func UseGenerated() string { return GeneratedID() }
