package d

func g() uint64 { return 1 }

var Counter = new(uint64(g()))

func (T) M() {}
