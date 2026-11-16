package a

import . "example.com/impl_dot_import/d"

// A name a dot import binds is a type of another package that nothing in the file says: it is not followed, so a method
// that takes one is not proven to be the interface's (b.go writes the same type with its package and is).

type Handler interface{ Handle(Req) }

type Dot struct{}

func (Dot) Handle(Req) {}

func Use(h Handler) {
	h.Handle(Req{}) // want: none
}
