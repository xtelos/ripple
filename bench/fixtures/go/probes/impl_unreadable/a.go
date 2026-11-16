package a

import "example.com/impl_unreadable/clean"

// tree-sitter-go cannot read generic.go (Go 1.26's new(expr)), and its recovery swallows what follows, so what the
// package declares is not all known: its own types are not known to have no other methods, and what it does not
// declare may be declared in the file ripple could not read.

type Runner interface{ Run() }

type Here struct{}

func (Here) Run() {}

// implements clean.Runner, in a package ripple could not read completely
type Inside struct{}

func (Inside) Go() {}

func Use(r Runner, c clean.Runner) {
	r.Run() // want: none
	c.Go()  // want: clean:Fine.Go
}
