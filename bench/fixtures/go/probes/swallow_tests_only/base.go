package sto

type Base struct{}

func (Base) M() {}

type T struct{ Base }
