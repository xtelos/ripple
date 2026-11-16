package main

import "example.com/impl_cgo_link/a"

type Mine struct{}

func (Mine) M() {}

func main() {
	var i a.I = a.Own{}
	i.M() // want: a:Own.M, cmd/c:Mine.M
}
