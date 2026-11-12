package cp

/*
#include <stdio.h>
*/
import "C"

func Use() {
	C.puts(C.CString("x"))
}
