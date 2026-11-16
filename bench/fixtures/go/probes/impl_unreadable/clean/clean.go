package clean

type Runner interface{ Go() }

type Fine struct{}

func (Fine) Go() {}
