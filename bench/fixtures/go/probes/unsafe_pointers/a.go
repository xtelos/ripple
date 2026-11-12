package a

import "unsafe"

type T struct {
	a int8
	b int64
}

func (*T) M() {}

func Use() {
	var t T
	_ = unsafe.Sizeof(t)
	_ = unsafe.Offsetof(t.b)
	p := unsafe.Pointer(&t)
	(*T)(p).M()
	q := (*T)(unsafe.Pointer(uintptr(p)))
	q.M()
	b := []byte("hi")
	_ = unsafe.String(&b[0], 2)
	_ = unsafe.Slice(&b[0], 2)
	_ = unsafe.Add(p, 1)
}
