package a

type I interface{ M() }

func Use(i I) {
	i.M() // want: none
}
