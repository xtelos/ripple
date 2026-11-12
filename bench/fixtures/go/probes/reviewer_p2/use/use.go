package use

import (
	"bytes"
	"io"
	"os"
	"strings"

	l "example.com/p2/lib"
	. "example.com/p2/strs"
)

type Local struct{}

func (Local) Close() error { return nil }
func (Local) Run()         {}

func Imports() {
	l.Helper()
	t := l.New()
	t.Len()
	t.Close()
	_ = Contains("a", "b") // dot import: strs.Contains
	_ = strings.Contains("a", "b")
}

func LocalNamedLikePackage() {
	strings := Local{}
	strings.Close() // Local.Close
	l := Local{}
	l.Run() // Local.Run
}

func External(f *os.File, r io.Reader, w io.WriteCloser) {
	f.Close()     // external (os.File concrete)
	w.Close()     // interface outside: may be Local/Thing; must not edge
	var b bytes.Buffer
	b.String()   // external
	var sb strings.Builder
	sb.String()
	_ = r
}

func Generics() {
	b := l.Make(1)
	b.Get()
	b.Put(2)
	var bb l.Box[string]
	bb.Get()
	_ = l.Map(1, func(i int) string { return "" })
	g := l.Map[int, string]
	_ = g(1, nil)
	f := l.Make[int]
	_ = f(3)
}

func Ifaces(r l.Runner) {
	r.Run() // interface: unresolved
	var x any = l.Impl{}
	switch v := x.(type) {
	case l.Impl:
		v.Run() // Impl.Run
	case l.Runner:
		v.Run() // iface
	case Local, *l.Impl:
		_ = v
	}
	if i, ok := x.(l.Impl); ok {
		i.Run()
	}
	x.(l.Runner).Run()
	x.(Local).Run()
}

func MethodValue() {
	t := l.Thing{}
	f := t.String
	_ = f()
	g := (*l.Thing).Len
	_ = g(&t)
	_ = l.Thing.String(t)
	_ = (*l.Thing).Len(&t)
}

func NonStruct() {
	n := l.Num(3)
	n.Double().Double()
}

func Closures() {
	x := Local{}
	f := func() { x.Run() }
	f()
	defer x.Close()
	go x.Run()
}

func Ranges(m map[string]Local, s []*Local, ch chan Local) {
	for _, v := range m {
		v.Run()
	}
	for _, v := range s {
		v.Run()
	}
	for v := range ch {
		v.Run()
	}
	(<-ch).Run()
	m["a"].Run()
	s[0].Run()
}
