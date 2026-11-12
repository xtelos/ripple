package a

type Color int

const Red Color = iota

func (Color) String() string { return "" }

func Use() {
	_ = Red.String()
	const c Color = 1
	_ = c.String()
	var x Color = Red
	_ = x.String()
}
