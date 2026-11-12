package money

type Cents int64

func (c Cents) String() string { return format(int64(c)) }

func format(v int64) string { return "" }

func Sum(xs []Cents) Cents {
	var t Cents
	for _, x := range xs {
		t += x
	}
	return t
}

func Scale(c Cents, p int) Cents { return c * Cents(p) / 100 }
