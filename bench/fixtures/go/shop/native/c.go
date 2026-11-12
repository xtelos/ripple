package native

/*
#include <stdio.h>
*/
import "C"

func Puts(s string) {
	C.puts(C.CString(s))
}
