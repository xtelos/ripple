package f

import "testing"

func (Conn) Close() {}

func TestX(t *testing.T) {
	Shutdown(Conn{})
	var c Conn
	c.Close()
}
