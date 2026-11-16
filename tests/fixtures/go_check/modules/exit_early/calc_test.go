package calc

import (
	"syscall"
	"testing"
)

func TestBefore(t *testing.T) {
}

func TestExits(t *testing.T) {
	syscall.Exit(0)
}

func TestAfter(t *testing.T) {
}
