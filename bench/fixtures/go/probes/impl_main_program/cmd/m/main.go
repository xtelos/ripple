package main

import a "example.com/impl_main_program"

// this program links a and nothing else of the repo
type M struct{}

func (M) Run() {}

func main() {
	var r a.Runner = M{}
	r.Run() // want: Real.Run, cmd/m:M.Run
}
