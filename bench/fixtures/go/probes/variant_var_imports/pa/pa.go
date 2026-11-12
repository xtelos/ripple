package pa

type C struct{}

func (C) Do() {}
func New() *C { return &C{} }
