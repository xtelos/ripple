package ttc

func (n Num) Double() Num { return n * 2 }

func Use() {
	Num(3).Double()
}
