//go:build !linux

package tmc

func (T) M() { helperO() }

func helperO() {}
