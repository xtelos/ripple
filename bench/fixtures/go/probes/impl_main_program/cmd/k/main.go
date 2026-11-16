package main

import a "example.com/impl_main_program"

// the program links a alone, but the test binary of this directory links d as well, and has this call too
type K struct{}

func (K) Run() {}

func main() {
	var r a.Runner = K{}
	r.Run() // want: Real.Run, d:D.Run, cmd/k:K.Run
}
