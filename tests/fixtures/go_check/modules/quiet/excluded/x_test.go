//go:build integration

package excluded

import "testing"

func TestNever(t *testing.T) {
	t.Fatal("never")
}
