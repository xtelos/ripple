package cp

/*
#include <stdio.h>
*/
import "C"

// Native is compiled only when cgo is on (CGO_ENABLED=0 leaves the whole file out): with cgo the interface has an
// implementer, without it has none, so neither E1.A nor E3.B is reached by a call built wherever it is
type Native struct {
	E1
	E3
}

func Say() { C.puts(C.CString("x")) }
