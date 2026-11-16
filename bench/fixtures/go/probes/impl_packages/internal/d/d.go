package d

import (
	"context"

	"example.com/impl_packages"
)

type Hidden struct{}

func (Hidden) Do(ctx context.Context, job a.Job) error { return nil }

// a Job of its own: not the interface's
type Job struct{ ID int }

type Other struct{}

func (Other) Do(ctx context.Context, job Job) error { return nil }
