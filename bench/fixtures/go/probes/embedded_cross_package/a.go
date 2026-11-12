package a

import (
	"sync"

	"example.com/embedded_cross_package/lib"
)

type S struct {
	lib.Base
	sync.Mutex
}

type Locker struct{}

func (Locker) Lock() {}

func Use() {
	var s S
	s.Hello()
	s.Lock()
	s.Unlock()
	var l Locker
	l.Lock()
	var p lib.Plain
	p.Hello()
}
