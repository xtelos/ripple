package a

// The method set of a type is the one of a build. A type satisfies the interface for the call only if it does in every
// build the call is in, so a type or a method that only some builds have (a constraint, a GOOS file name, a _test.go
// file) is not followed, nor is a method declared once for each build, nor a file that imports "C".

type Runner interface {
	Start()
	Stop()
}

type Always struct{}

func (Always) Start() {}
func (Always) Stop()  {}

// Stop only on linux (split_linux.go): no Runner on windows or darwin
type Split struct{}

func (Split) Start() {}

// Stop once for every platform (twice_*.go): a Runner everywhere, but not one declaration
type Twice struct{}

func (Twice) Start() {}

func Use(r Runner) {
	r.Start() // want: Always.Start
	r.Stop()  // want: Always.Stop
}
