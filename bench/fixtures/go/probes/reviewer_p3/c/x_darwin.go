package c

import foo "example.com/p3/pa"

var X = foo.New()

func Make() *foo.C { return foo.New() }
