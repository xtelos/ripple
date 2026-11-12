//go:build !windows

package c

func (T) Only() {}

func init() {
	var t T
	t.Only()
}
