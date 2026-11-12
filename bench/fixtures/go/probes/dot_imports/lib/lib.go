package lib

func F() {}

type T struct{}

func (T) M() {}

func New() *T { return &T{} }

var V = New()
