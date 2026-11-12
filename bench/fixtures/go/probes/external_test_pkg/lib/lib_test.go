package lib_test

import (
	"testing"

	"example.com/external_test_pkg/lib"
)

type local struct{ lib.T }

func (local) Own() {}

func TestX(t *testing.T) {
	x := lib.New()
	x.M()
	x.Hook()
	var l local
	l.M()
	l.Own()
	lib.Exported()
}
