package b

import "testing"

func TestOK(t *testing.T) {
}

func BenchmarkBad(b *testing.B) {
	b.Fatal("bench broke")
}
