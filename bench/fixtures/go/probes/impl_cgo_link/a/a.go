package a

type I interface{ M() }

type Own struct{}

func (Own) M() {}
