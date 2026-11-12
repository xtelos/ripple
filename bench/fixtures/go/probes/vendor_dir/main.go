package main

import "example.org/dep"

type Own struct{}

func (Own) M() {}

func Helper() {}

func main() {
	Helper()
	dep.Helper()
	var t dep.T
	t.Only()
	t.M()
}
