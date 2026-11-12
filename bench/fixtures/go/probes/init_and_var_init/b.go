package a

func init() {
	record("init-b")
	a()
}

var third = made.Make()

func Use() {
	tbl["a"]()
	made.Run()
	third.Run()
}
