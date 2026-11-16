package a

import b "example.com/other"

type I interface{ M() }

func Use(i I) {
	i.M() // want: none
	b.F()
}
