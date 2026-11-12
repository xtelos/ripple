//go:build !windows

package a

func platform() {}

func (T) Name() string { return "unix" }

func init() { initOnly() }

func initOnly() {}
