package cg

/*
extern void goCallback(void);
static void callBack(void) { goCallback(); }
static int add(int a, int b) { return a + b; }
*/
import "C"

func hook() {}

//export goCallback
func goCallback() {
	hook()
}

type T struct{}

func (T) Len() int { return 1 }

func Use() int {
	C.callBack()
	n := C.add(1, 2)
	var t T
	return int(n) + t.Len()
}
