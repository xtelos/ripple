package lib

type Base struct{}

func (Base) Hello() {}

type Plain struct{ Base }
