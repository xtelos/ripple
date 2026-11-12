package d

func g() uint64 { return 1 }

func Broken() *uint64 { return new(uint64(g())) }

func (T) M() {}

func (T) N() { helper() }

func helper() {}
