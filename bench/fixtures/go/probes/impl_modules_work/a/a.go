package a

// a and b are one workspace: a library call here has b's types, but a program of a has only what it imports.

type Runner interface{ Run() }

type Own struct{}

func (Own) Run() {}

func Use(r Runner) {
	r.Run() // want: example.com/mw_a:Own.Run, example.com/mw_a/cmd/app:App.Run, example.com/mw_b:T.Run
}
