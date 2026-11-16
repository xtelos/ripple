package main

import (
	a "example.com/impl_main_program"
	"example.com/impl_main_program/b"
	"example.com/impl_main_program/internal/e"
)

// this program links a, b and e: c, d, f and the other programs' types are not in it
type N struct{}

func (N) Run() {}

func main() {
	var r a.Runner = N{}
	_, _ = b.Dep{}, e.E{}
	r.Run() // want: Real.Run, b:Dep.Run, internal/e:E.Run, cmd/n:N.Run
}
