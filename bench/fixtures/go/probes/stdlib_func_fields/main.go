package main

import (
	"flag"
	"net/http"
	"os/exec"
	"sync"
)

func printHelp() {}

func main() {
	var fs flag.FlagSet
	fs.Usage = func() { printHelp() }
	fs.Usage()
	_ = fs.Parse(nil)
	flag.Usage = printHelp
	flag.Usage()
	var c exec.Cmd
	c.Cancel = func() error { printHelp(); return nil }
	_ = c.Cancel()
	p := sync.Pool{New: func() any { printHelp(); return 1 }}
	_ = p.New()
	_ = p.Get()
	var cl http.Client
	cl.CheckRedirect = func(req *http.Request, via []*http.Request) error { printHelp(); return nil }
	_ = cl.CheckRedirect(nil, nil)
	var wg sync.WaitGroup
	wg.Wait()
}
