//go:build !linux

package tia

import foo "example.com/twice_import_alias/otherlib"

func Run() {
	foo.Do()
}
