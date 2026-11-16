package a

// An unexported method name belongs to the package that writes it: only a type that has THAT package's method can
// satisfy the interface, and a method of the same spelling in another package is another method.

type Sealed interface {
	seal()
	Name() string
}

type One struct{}

func (One) seal()        {}
func (One) Name() string { return "" }

// seal() only: a type of another package can get it by embedding
type Seal struct{}

func (Seal) seal() {}

func Use(s Sealed) {
	s.seal()   // want: One.seal, Seal.seal
	s.Name()   // want: One.Name, b:Mixed.Name
}
