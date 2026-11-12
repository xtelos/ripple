package inventory

import . "strings"

// Shout calls names of a dot-imported package from outside the repo.
func Shout(s string) string { return ToUpper(Repeat(s, 2)) }
