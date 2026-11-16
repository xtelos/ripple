package a

import "fmt"

// An interface that embeds others has the methods of all of them. Ripple follows an embedded repo interface that it
// reads whole; one from outside the repo (fmt.Stringer, the universe's error), a union, a `~T` and a generic instance
// leave the method set not known completely, so no type is proven to satisfy it.

type Reader interface{ Read() string }
type Writer interface{ Write(s string) }

type ReadWriter interface {
	Reader
	Writer
}

type File struct{}

func (File) Read() string { return "" }
func (File) Write(string) {}

type Half struct{}

func (Half) Read() string { return "" }

// the same method embedded twice by two paths, and written again: one method
type Left interface{ Reader }
type Right interface{ Reader }
type Diamond interface {
	Left
	Right
	Read() string
}

// a named interface type, and an alias of one
type Named Reader
type Alias = Reader

// from outside the repo
type Shown interface {
	fmt.Stringer
	Extra() int
}

type Thing struct{}

func (Thing) String() string { return "" }
func (Thing) Extra() int     { return 0 }

type Failure interface {
	error
	Code() int
}

type Oops struct{}

func (Oops) Error() string { return "" }
func (Oops) Code() int     { return 1 }

// a constraint, never a value's type
type Number interface{ ~int | ~float64 }

type Labelled interface {
	~int
	Label() string
}

type Level int

func (Level) Label() string { return "" }

func Show[T Labelled](x T) string { return x.Label() } // want: none

func Use(rw ReadWriter, d Diamond, n Named, al Alias, s Shown, f Failure) {
	rw.Read()     // want: File.Read
	rw.Write("x") // want: File.Write
	d.Read()      // want: File.Read, Half.Read
	n.Read()      // want: File.Read, Half.Read
	al.Read()     // want: File.Read, Half.Read
	s.Extra()     // want: none
	f.Code()      // want: none
}
