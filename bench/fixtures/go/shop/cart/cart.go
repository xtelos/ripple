package cart

import (
	"errors"
	"fmt"
	"sync"

	"example.com/shop/internal/money"
	"example.com/shop/pricing"
)

type Base struct{ mu sync.Mutex }

func (b *Base) Lock()   { b.mu.Lock() }
func (b *Base) Unlock() { b.mu.Unlock() }

type Audited struct {
	Base
	log []string
}

func (a *Audited) Record(s string) {
	a.Lock()
	a.log = append(a.log, s)
	a.Unlock()
}

type Cart struct {
	Audited
	items []money.Cents
	rules []pricing.Rule
	onAdd func(money.Cents)
}

func New() *Cart { return &Cart{} }

func NewWithErr() (*Cart, error) { return New(), nil }

func (c *Cart) Add(x money.Cents) {
	c.items = append(c.items, x)
	c.Record(x.String())
	if c.onAdd != nil {
		c.onAdd(x)
	}
}

func (c *Cart) Total() money.Cents { return pricing.Total(c.items, c.rules) }

type CartError struct{ Msg string }

func (e *CartError) Error() string { return e.Msg }

func Checkout(c *Cart) error {
	defer c.Unlock()
	c.Lock()
	var err error = &CartError{"x"}
	if c.Total() == 0 {
		err = errors.New("empty")
	}
	fmt.Println(err.Error())
	go audit(c)
	return err
}

func audit(c *Cart) { c.Record("checkout") }

func Run() {
	c, err := NewWithErr()
	if err != nil {
		return
	}
	c.Add(3)
	f := c.Total
	f()
	add := func(x money.Cents) { c.Add(x) }
	add(4)
	func() { c.Add(5) }()
	(*Cart).Add(c, 6)
	var r pricing.Rule = pricing.Percent{P: 1}
	r, ok := r.(pricing.Percent)
	if ok {
		r.Apply(7)
	}
}

func Local() {
	New := func() *Cart { return nil }
	_ = New()
}

func double(x int) int { return x * 2 }

func UseGenerics() {
	_ = pricing.Map([]int{1}, double)
	_ = pricing.Pair[string, int]("a", 1)
}

var defaultCart = New()

func init() { defaultCart.Add(1) }

func init() { register() }

func register() {}

func Platform() string { return platform() }

func UseStrategy() money.Cents { return Strategy.Apply(1) }

func Describe(v any, ch chan *Cart) {
	switch x := v.(type) {
	case *Cart:
		x.Add(1)
	case pricing.Rule:
		x.Apply(1)
	}
	select {
	case c := <-ch:
		c.Add(2)
	}
}
