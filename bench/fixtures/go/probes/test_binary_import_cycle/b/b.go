package b

import a "example.com/test_binary_import_cycle"

var _ = a.Run

type T struct{}

func (T) M() {}
