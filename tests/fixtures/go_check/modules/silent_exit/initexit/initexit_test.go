package initexit

import (
	"os"
	"testing"
)

func init() {
	os.Exit(0)
}

func TestAlsoBroken(t *testing.T) {
	t.Fatal("never runs")
}
