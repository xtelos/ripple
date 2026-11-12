package tcc

type Level int

func (l Level) String() string { return "" }

func Use() {
	_ = Level(Mode).String()
}
