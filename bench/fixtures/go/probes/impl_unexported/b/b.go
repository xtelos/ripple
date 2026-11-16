package b

import a "example.com/impl_unexported"

// its own seal is not a.Sealed's seal
type Foreign struct{}

func (Foreign) seal()        {}
func (Foreign) Name() string { return "" }

// a.Seal's seal is promoted in, and it is a.Sealed's
type Mixed struct{ a.Seal }

func (Mixed) Name() string { return "" }

func Use(s a.Sealed) {
	s.Name() // want: One.Name, b:Mixed.Name
}
