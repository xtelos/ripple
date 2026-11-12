package otp

import "github.com/spf13/pflag"

func hook() {}

func Use() {
	pflag.Usage = hook
	pflag.Usage()
	pflag.Parse()
	fs := pflag.NewFlagSet("x", pflag.ContinueOnError)
	fs.Usage = hook
	fs.Usage()
	fs.Bool("b", false, "")
	fs.Parse(nil)
}
