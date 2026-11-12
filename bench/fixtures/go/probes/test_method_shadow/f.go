package f

type Base struct{}

func (Base) Close() {}

type Conn struct{ Base }

func Shutdown(c Conn) {
	c.Close()
}
