package f

type Base struct{}

func (Base) Close() {}

type Conn struct{ Base }

func Shutdown(c Conn) {
	c.Close() // go build: Base.Close; go test: Conn.Close from the _test.go file
}
