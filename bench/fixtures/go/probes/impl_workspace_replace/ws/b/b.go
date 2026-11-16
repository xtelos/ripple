package b

import c "example.com/c"

type I interface{ M() }

func Use(i I) {
	i.M() // want: example.com/c:T.M
	c.F()
}
