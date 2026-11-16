package a

// A type that is not a struct has a method set of its own, a defined type does not inherit the methods of the type it is
// defined from, and an alias is the type it names.

type Handler interface{ Handle(n int) string }

type HandlerFunc func(n int) string

func (f HandlerFunc) Handle(n int) string { return f(n) }

type List []string

func (l List) Handle(n int) string { return l[n] }

// defined from List: List's methods are not Sorted's
type Sorted List

// a struct that embeds List gets Handle promoted
type Wrapped struct{ List }

// defined from a struct with an embedded field: the embedded field's methods come along, the struct's own do not
type Base struct{}

func (Base) Handle(n int) string { return "" }

type Outer struct{ Base }

func (Outer) Extra() {}

type Copy Outer

// an alias is the very type, and its own method is the target's
type Same = Base

func Use(h Handler) {
	h.Handle(1) // want: HandlerFunc.Handle, List.Handle, Base.Handle
}
