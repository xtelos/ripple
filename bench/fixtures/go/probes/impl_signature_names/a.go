package a

// Two types written with one name are one type only when they are one declaration: the same name in two packages, a
// package that declares a type called byte, and two stdlib packages both imported as `template` are not.

type Item struct{}

type Taker interface{ Take(Item) }

type Local struct{}

func (Local) Take(Item) {}

func Use(t Taker) {
	t.Take(Item{}) // want: Local.Take, b:Imports.Take
}
