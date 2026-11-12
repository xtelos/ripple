//go:build !linux

package tfr

type B struct{}

func (B) Run() {}

func New() B { return B{} }
