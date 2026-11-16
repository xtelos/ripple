package b

import (
	"testing"

	_ "example.com/test_binary_import_cycle/c"
)

func TestB(t *testing.T) {}
