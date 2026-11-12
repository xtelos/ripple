package a

type T struct{}

func Use() {
	platform()
	var t T
	_ = t.Name()
	initOnly()
}
