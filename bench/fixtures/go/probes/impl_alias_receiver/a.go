package a

// A method declared on an alias is a method of the type the alias names, and keeps the id of the alias (A.Run). A type
// whose alias is declared twice for different builds has methods nobody can place, so none is followed.

type Runner interface{ Run() }

type B struct{}

type A = B

func (A) Run() {}

type C struct{}

type D = C

func (D) Run() {}

func Use(r Runner) {
	r.Run() // want: A.Run, D.Run
}
