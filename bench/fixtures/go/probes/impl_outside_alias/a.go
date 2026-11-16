package a

import pf "github.com/spf13/pflag"

// A type from outside the repo is one type for an import path and a name, whatever alias each file gives the import.
// Without an alias the path is only guessed from the name the file uses (its package clause may say another), and that is
// proven for the standard library alone: so Plain, which compiles with the interface, is not proven to.

type Setter interface{ Set(*pf.FlagSet) }

type Same struct{}

func (Same) Set(*pf.FlagSet) {}

type Other struct{}

func (Other) Set(*pf.Flag) {}

type Applier interface{ Apply(*pf.FlagSet) }

func Use(s Setter, p Applier) {
	s.Set(nil)   // want: Same.Set
	p.Apply(nil) // want: none
}
