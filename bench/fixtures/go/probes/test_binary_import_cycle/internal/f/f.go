package f

type F struct{}

func (F) M() {}

type J interface{ M() }

func G(j J) {
	j.M()
}
