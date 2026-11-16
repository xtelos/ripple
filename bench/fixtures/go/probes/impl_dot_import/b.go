package a

import "example.com/impl_dot_import/d"

type Qualified interface{ Serve(d.Req) }

type Named struct{}

func (Named) Serve(d.Req) {}

func UseQualified(q Qualified) {
	q.Serve(d.Req{}) // want: Named.Serve
}
