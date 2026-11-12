package a

import (
	autil "example.com/renamed_imports/a/util"
	butil "example.com/renamed_imports/b/util"
	_ "example.com/renamed_imports/c"
	"example.com/renamed_imports/x/libx"
)

func Use() {
	autil.Do()
	butil.Do()
	xlib.F()
}

func Shadowed() {
	autil := func() {}
	autil()
}
