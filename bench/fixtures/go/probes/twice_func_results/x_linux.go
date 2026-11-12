package tfr

type A struct{}

func (A) Run() {}

func New() A { return A{} }
