package taf

type X struct{ Hook func() }

type Base struct{}

func (Base) Hook() {}

type Y struct{ Base }

func Use(a A) {
	a.Hook()
}
