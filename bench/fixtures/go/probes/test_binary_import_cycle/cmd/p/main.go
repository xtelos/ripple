package main

import (
	_ "example.com/test_binary_import_cycle/internal/f"
	_ "example.com/test_binary_import_cycle/c"
)

func main() {}
