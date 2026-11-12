package c

func UseResult() {
	m := Make()
	m.Do()
	Make().Do()
}
