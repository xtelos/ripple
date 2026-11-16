package a

type I interface{ M() }

func Run(i I) {
	i.M()
}
