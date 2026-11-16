package a

type Runner interface{ Run() }

type Real struct{}

func (Real) Run() {}
