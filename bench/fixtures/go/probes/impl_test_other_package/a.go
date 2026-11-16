package a

// The test files of a package are built into its own test binary, and into no other package's: a type declared in
// b's _test.go is not a candidate for a call in a's test, which is built with b as it is without tests. A test binary
// has the packages its files import and no other, and a package main is nobody's import: its type is a candidate only
// for a call of a package the program imports.

type Runner interface{ Run() }

type Real struct{}

func (Real) Run() {}

func Use(r Runner) {
	r.Run() // want: Real.Run, b:Prod.Run, c:Loner.Run, cmd/n:N.Run
}
