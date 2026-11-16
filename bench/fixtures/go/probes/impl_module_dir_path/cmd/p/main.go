package main

import "example.com/y/pkg"

type I interface{ M() }

func main() {
	pkg.F()
	var i I
	i.M() // want: example.com/y/pkg:T.M
}
