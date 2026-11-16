package a

// Methods a struct gets from the types it embeds, by the spec's depth rule: the shallowest depth wins, two at one depth
// hide each other, a field hides a method of its name, and the method set of the whole type has to have every method of
// the interface for the call to reach any of them.

type Session interface {
	Open() error
	Close() error
}

// Part has Open only: no Session by itself
type Part struct{}

func (Part) Open() error { return nil }

// Whole adds Close: Part's Open is promoted into it
type Whole struct{ Part }

func (Whole) Close() error { return nil }

// two levels down
type Mid struct{ Part }
type Deep struct{ Mid }

func (Deep) Close() error { return nil }

// through an embedded pointer
type PtrWhole struct{ *Part }

func (PtrWhole) Close() error { return nil }

// its own Open has another signature, and hides Part's
type Shadowed struct{ Part }

func (Shadowed) Open(n int) error { return nil }
func (Shadowed) Close() error     { return nil }

// a field of the name hides the promoted method
type FieldShadow struct {
	Part
	Open int
}

func (FieldShadow) Close() error { return nil }

// two embedded types at one depth both have Open: neither is promoted
type Other struct{}

func (Other) Open() error { return nil }

type Ambiguous struct {
	Part
	Other
}

func (Ambiguous) Close() error { return nil }

// the same two, one deeper than the other: the shallow one wins
type Wrap struct{ Other }
type Unambiguous struct {
	Part
	Wrap
}

func (Unambiguous) Close() error { return nil }

// Open from an embedded interface, which has no source here
type Opener interface{ Open() error }
type ViaInterface struct{ Opener }

func (ViaInterface) Close() error { return nil }

func Use(s Session) {
	s.Open()  // want: Part.Open
	s.Close() // want: Whole.Close, Deep.Close, PtrWhole.Close, Unambiguous.Close, ViaInterface.Close
}
