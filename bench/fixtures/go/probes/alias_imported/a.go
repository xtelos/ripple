package a

import "example.com/alias_imported/lib"

type A = lib.T
type P = *lib.T

func Use() {
	var a A
	a.M()
	var p P
	p.M()
	x := lib.New()
	x.M()
}
