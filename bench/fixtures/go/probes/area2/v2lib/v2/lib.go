package lib

type L struct{}

func (L) Do() {}

func Make() L { return L{} }
