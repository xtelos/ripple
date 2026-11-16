package b

import "testing"

func TestOK(t *testing.T) {
}

func BenchmarkGood(b *testing.B) {
	for i := 0; i < b.N; i++ {
	}
}

func BenchmarkSkipped(b *testing.B) {
	b.Skip("no")
}
