package d

import _ "example.com/test_binary_import_cycle/internal/e"

type D struct{}

func (D) M() {}
