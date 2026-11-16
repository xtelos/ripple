package fuzzonly

import "testing"

func FuzzX(f *testing.F) {
	f.Add(1)
	f.Fuzz(func(t *testing.T, n int) {})
}
