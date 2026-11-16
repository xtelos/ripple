package main

import (
	"context"

	"example.com/impl_packages"
	_ "example.com/impl_packages/internal/d"
)

type Main struct{}

func (Main) Do(ctx context.Context, job a.Job) error { return nil }

func main() {
	var d a.Doer = Main{}
	d.Do(context.TODO(), a.Job{}) // want: Local.Do, internal/d:Hidden.Do, cmd:Main.Do
}
