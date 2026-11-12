package a

import (
	. "example.com/dot_imports/lib"
	. "strings"
)

func Use() {
	F()
	var t T
	t.M()
	New().M()
	V.M()
	_ = Contains("a", "b")
	_ = ToUpper("a")
}

func Shadow() {
	F := func() {}
	F()
	T := 3
	_ = T
}
