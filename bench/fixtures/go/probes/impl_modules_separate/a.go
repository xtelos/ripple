package a

// b is a module nobody joins to this one (no replace, no go.work): the build of this module has its published version,
// which may differ from the checkout, so its T is no candidate. c's go.mod replaces this module by the checkout: a build of
// c has both, so C is.

type I interface{ Close() error }

type Own struct{}

func (Own) Close() error { return nil }

func Use(i I) {
	i.Close() // want: Own.Close, example.com/other2:C.Close
}
