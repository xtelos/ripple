package main

// a program that does not import package a: its M is never in a build with a's call
type M struct{}

func (M) Run() {}

func main() {
	_ = M{}
}
