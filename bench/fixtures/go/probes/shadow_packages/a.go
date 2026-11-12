package a

import (
	"fmt"
	"strings"

	"example.com/shadow_packages/lib"
)

type Local struct{}

func (Local) Close() {}
func (Local) Run()   {}

func Use(fmt Local) {
	fmt.Close()
	_ = strings.Contains("a", "b")
}

func Use2() {
	lib := Local{}
	lib.Run()
	{
		x := lib
		x.Close()
	}
}

func Use3() {
	lib.Run()
	strings := Local{}
	strings.Run()
}

func Use4() {
	fmt.Println()
}
