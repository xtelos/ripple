package f

type Base struct{}

func (Base) Close() {}

type Conn struct{ Base }
