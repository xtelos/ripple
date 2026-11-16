package calc

import (
	"testing"
	"time"
)

func TestFast(t *testing.T) {
}

func TestSlow(t *testing.T) {
	time.Sleep(30 * time.Second)
}
