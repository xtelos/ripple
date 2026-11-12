package a

type Base struct{}

func (Base) M() {}

type Box[T any] struct{ Base }

type Alias[T any] = Box[T]

func (Box[T]) M() {}
