package inventory

func (g *Gadget) Ping() {}

// UseGadget calls a method declared once, and a struct field that only one build declares: which Gadget wins is not known.
func UseGadget(g *Gadget) {
	g.Ping()
	g.hook()
}
