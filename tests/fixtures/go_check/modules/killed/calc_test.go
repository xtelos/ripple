package calc

import (
	"syscall"
	"testing"
	"time"
)

func TestBefore(t *testing.T) {
}

func TestKilled(t *testing.T) {
	syscall.Kill(syscall.Getpid(), syscall.SIGKILL)
	time.Sleep(10 * time.Second)
}
