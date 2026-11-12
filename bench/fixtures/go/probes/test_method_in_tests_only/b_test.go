package f

import "testing"

func TestX(t *testing.T) {
	var c Conn
	c.Close()
	var b Base
	b.Close()
}
