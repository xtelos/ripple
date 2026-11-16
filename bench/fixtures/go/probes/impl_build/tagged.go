//go:build linux || windows || darwin

package a

// built on every platform the probe is checked for, but by a constraint ripple does not evaluate
type Tagged struct{}

func (Tagged) Start() {}
func (Tagged) Stop()  {}
