package a_test

import (
	"testing"

	_ "example.com/test_binary_import_cycle/b"
	_ "example.com/test_binary_import_cycle/d"
)

func TestY(t *testing.T) {}
