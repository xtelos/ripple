package d

func g() uint64 { return 1 }

func (T) M() {}

func (T) N() { helper() }

func helper() {}

func Broken() *uint64 { return new(uint64(g())) }
