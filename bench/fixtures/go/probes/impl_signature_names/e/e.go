package e

import . "strings"

// a name a dot import may bind is not followed
type Writer interface{ Write(*Builder) }

type Dotted struct{}

func (Dotted) Write(*Builder) {}

func Use(w Writer) {
	w.Write(nil) // want: none
}
