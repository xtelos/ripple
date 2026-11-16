package a

type T11 struct{}

func (T11) M11() {}

type I11 interface {
	M11()
	N11()
}

type T12 struct{}

func (T12) M12() {}

type I12 interface {
	M12()
	N12()
}
