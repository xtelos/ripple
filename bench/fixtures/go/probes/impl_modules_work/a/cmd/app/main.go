package main

import a "example.com/mw_a"

type App struct{}

func (App) Run() {}

func main() {
	var r a.Runner = App{}
	r.Run() // want: example.com/mw_a:Own.Run, example.com/mw_a/cmd/app:App.Run
}
