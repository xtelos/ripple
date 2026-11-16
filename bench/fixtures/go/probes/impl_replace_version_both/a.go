package a

import b "example.com/other"

type I interface{ M() }

func Use(i I) {
	i.M() // want: example.com/other:T.M
	b.F()
}
