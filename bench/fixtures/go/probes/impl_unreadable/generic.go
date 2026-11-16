package a

func size() uint64 { return 1 }

var Counter = new(uint64(size()))

func (Inside) Extra() {}
