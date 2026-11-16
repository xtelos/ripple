package main

import a "example.com/impl_test_other_package"

type N struct{}

func (N) Run() {}

func main() {
	var r a.Runner = N{}
	r.Run() // want: Real.Run, cmd/n:N.Run
}
