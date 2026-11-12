package main

import (
	"flag"
	"os/exec"
)

func printHelp() {}

func main() {
	fs := flag.NewFlagSet("x", flag.ExitOnError)
	var fs2 flag.FlagSet
	fs2.Usage = func() { printHelp() }
	fs2.Usage()
	flag.Usage = printHelp
	flag.Usage()
	var c exec.Cmd
	c.Cancel = func() error { printHelp(); return nil }
	_ = c.Cancel()
	_ = fs
}
