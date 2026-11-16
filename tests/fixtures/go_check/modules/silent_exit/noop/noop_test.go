package noop

import (
	"os"
	"testing"
)

func TestMain(m *testing.M) {
	os.Exit(0)
}

func TestBroken(t *testing.T) {
	t.Fatal("never runs")
}
