package util

type T struct{}

func (T) M() {}

func Do() {}

func New() *T { return &T{} }
