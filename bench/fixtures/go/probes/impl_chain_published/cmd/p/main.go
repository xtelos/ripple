package main

import "example.com/a"

type I interface{ M() }

func main() {
	a.G()
	var i I
	i.M() // want: none
}
