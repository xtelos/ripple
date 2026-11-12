package a

import "testing"

type fake struct{ T }

func (fake) Extra() {}

func helper() {
	Run()
}

func TestRun(t *testing.T) {
	var f fake
	f.M()
	f.Extra()
	helper()
	t.Helper()
	t.Log("x")
}
