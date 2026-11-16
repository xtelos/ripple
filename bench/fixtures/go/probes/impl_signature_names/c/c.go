package c

// this package declares a type called byte: []byte here is not []uint8
type byte struct{}

type Feeder interface{ Feed([]byte) }

type Plain struct{}

func (Plain) Feed([]uint8) {}

type Same struct{}

func (Same) Feed([]byte) {}

func Use(f Feeder) {
	f.Feed(nil) // want: c:Same.Feed
}
