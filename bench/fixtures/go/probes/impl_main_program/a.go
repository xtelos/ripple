package a

// A call in a package main is in that program (and in the test binary of its directory) and nowhere else: nobody imports
// a main package, and a program links exactly the packages it imports, transitively. A call in a library package is in every
// program that links it, and in a program of another repository that imports it, so a type is a candidate when some unit
// of the repo links it with the call's package, or both packages can be imported from outside (neither is a main package
// or below an internal directory). Internal/f is imported by nobody and below internal: no build has it and this call.

type Runner interface{ Run() }

type Real struct{}

func (Real) Run() {}

func Use(r Runner) {
	r.Run() // want: Real.Run, b:Dep.Run, c:Loner.Run, d:D.Run, internal/e:E.Run, cmd/k:K.Run, cmd/m:M.Run, cmd/n:N.Run
}
