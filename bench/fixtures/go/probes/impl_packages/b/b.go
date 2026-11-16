package b

import (
	"context"

	core "example.com/impl_packages"
)

type Remote struct{}

func (*Remote) Do(ctx context.Context, job core.Job) error { return nil }

func Call(d core.Doer) {
	d.Do(nil, core.Job{}) // want: Local.Do, b:Remote.Do
}
