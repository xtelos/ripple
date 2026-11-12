//go:build !gadgetb

package inventory

type Gadget struct {
	n    int
	hook func()
}

func (g *Gadget) Extra() {}
