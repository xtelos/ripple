package a

// The canonical text of a channel type reads the tokens as Go does: the arrow binds to the leftmost chan, so `chan <-chan int` is
// `chan<- chan int` (a send-only channel of channels) and `chan (<-chan int)` is another type.

type TI struct{}

func (TI) M(c chan (<-chan int)) {}

type TJ struct{}

func (TJ) M(c chan<- chan int) {}

type TK struct{}

func (TK) M(c chan <-chan int) {}

type TL struct{}

func (TL) M(c <-chan (<-chan int)) {}

type TO struct{}

func (TO) M(c chan<- <-chan int) {}

type TQ struct{}

func (TQ) M(c <-chan chan<- int) {}

type TR struct{}

func (TR) M(c chan chan int) {}

type TS struct{}

func (TS) M(c chan (chan int)) {}

type TU struct{}

func (TU) M(c chan<- (chan int)) {}

type TV struct{}

func (TV) M(c <-chan <-chan int) {}

type I interface{ M(c chan (<-chan int)) }

type J interface{ M(c chan<- chan int) }

type K interface{ M(c chan <-chan int) }

type L interface{ M(c <-chan (<-chan int)) }

type O interface{ M(c chan<- <-chan int) }

type Q interface{ M(c <-chan chan<- int) }

type R interface{ M(c chan chan int) }

func Use(vI I, cI chan (<-chan int), vJ J, cJ chan<- chan int, vK K, cK chan <-chan int, vL L, cL <-chan (<-chan int), vO O, cO chan<- <-chan int, vQ Q, cQ <-chan chan<- int, vR R, cR chan chan int) {
	vI.M(cI) // want: TI.M
	vJ.M(cJ) // want: TJ.M, TK.M, TU.M
	vK.M(cK) // want: TJ.M, TK.M, TU.M
	vL.M(cL) // want: TL.M, TV.M
	vO.M(cO) // want: TO.M
	vQ.M(cQ) // want: TQ.M
	vR.M(cR) // want: TR.M, TS.M
}
