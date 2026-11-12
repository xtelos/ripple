package a

func Use() {
	var b Box[int]
	b.M()
	var a Alias[int]
	a.M()
}
