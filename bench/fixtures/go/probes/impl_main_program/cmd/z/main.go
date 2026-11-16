package main

// does not import a: its Z is in no build with a's call
type Z struct{}

func (Z) Run() {}

func main() {
	_ = Z{}
}
