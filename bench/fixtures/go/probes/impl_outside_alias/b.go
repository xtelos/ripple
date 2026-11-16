package a

import "github.com/spf13/pflag"

type Plain struct{}

func (Plain) Apply(*pflag.FlagSet) {}
