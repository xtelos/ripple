package a

import "context"

// The implementers of an interface are found in every package of the repo, whatever alias that package imports it by, in
// internal packages and in package main (an internal package only when a build links it with the call: here the program of cmd
// does, b does not); the signature's types are compared as the declarations they name.

type Doer interface {
	Do(ctx context.Context, job Job) error
}

type Job struct{ ID int }

type Local struct{}

func (Local) Do(ctx context.Context, job Job) error { return nil }

func Run(d Doer) {
	d.Do(context.Background(), Job{}) // want: Local.Do, b:Remote.Do, internal/d:Hidden.Do, cmd:Main.Do
}
