package b

import a "example.com/impl_signature_names"

// its own Item is not a.Item
type Item struct{}

type Own struct{}

func (Own) Take(Item) {}

type Imports struct{}

func (Imports) Take(a.Item) {}
