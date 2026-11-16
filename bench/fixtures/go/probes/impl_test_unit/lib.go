package lib

// The call in Call is in the test binary of every package that links lib, and in lib's own: an internal package that only a
// test of lib or of another package links (onlyx) is a candidate through that test binary, one that nothing links (lonely)
// is not. A call in a _test.go file is in its own directory's test binary alone: it has what that binary links (helper, which
// lib's test imports) and not what another package's test links (onlyx, y).

type Doer interface{ Do() }

type Own struct{}

func (Own) Do() {}

func Call(d Doer) {
	d.Do() // want: Own.Do, internal/helper:H.Do, internal/onlyx:OX.Do, y:Y.Do
}
