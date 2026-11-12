package lib

type T struct{}

func (T) M() {}

func New() *T { return &T{} }
