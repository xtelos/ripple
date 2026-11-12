package d

import "example.com/parse_error_other_package/lib"

var Counter = new(uint64(1))

type T struct{ lib.Base }

func (T) M() {}
