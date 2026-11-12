package d

func g() uint64 { return 1 }

func (T) M() {}

func (T) N() { helper() }

func helper() {}

var Counter = new(uint64(g()))
