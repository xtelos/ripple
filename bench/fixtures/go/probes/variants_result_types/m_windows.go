package c

func Make() *B { return &B{} }

func Pair() (*B, error) { return &B{}, nil }
