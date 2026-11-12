//go:build !flagged

package inventory

var Flagged = func() bool { return false }

func UseFlag() bool { return Flagged() }
