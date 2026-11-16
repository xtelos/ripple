package a

// A _test.go file is compiled into its package's test binary whether or not it also carries a build constraint, and that
// binary has the packages its files import and no others: b, which nobody here imports, is not in it. The constraint
// makes the file constrained, not the less a test file (a test binary is built from it all the same).

type Runner interface{ Run() }

type Real struct{}

func (Real) Run() {}
