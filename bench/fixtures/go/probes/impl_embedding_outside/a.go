package a

import (
	"bytes"
	"strings"
	"sync"
)

// An embedded type from outside the repo may provide a method of the interface: at a shallower depth it takes the name
// first, at the same depth it ties with the repo's (and neither is promoted). Ripple does not read the outside type, so
// a method found below such an embedding is not proven, and a type that needs one from the outside has no edge either.

type Sizer interface {
	Len() int
	Name() string
}

type Part struct{}

func (Part) Len() int { return 0 }

// Len comes from Part and from bytes.Buffer at one depth: neither is promoted, so Tie is no Sizer
type Tie struct {
	Part
	bytes.Buffer
}

func (Tie) Name() string { return "" }

// Name is Named's, and Len is bytes.Buffer's own: a Sizer, with the repo's Name
type Named struct{}

func (Named) Name() string { return "" }

type Borrowed struct {
	Named
	bytes.Buffer
}

// the repo's method is at depth 0 and the outside type's is deeper: it wins, whatever the outside type has
type Near struct{ strings.Builder }

func (Near) Len() int     { return 1 }
func (Near) Name() string { return "" }

// an embedded type from outside that has neither
type Quiet struct{ sync.Mutex }

func (Quiet) Len() int     { return 2 }
func (Quiet) Name() string { return "" }

func Use(s Sizer) {
	s.Len()  // want: Near.Len, Quiet.Len
	s.Name() // want: Near.Name, Quiet.Name
}
