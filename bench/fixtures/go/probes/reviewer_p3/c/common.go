package c

type Base struct{}

func (Base) M() {}

type T struct{ Base }

func UseVar() {
	X.Do() // linux: pa.C.Do, windows: pb.C.Do
}

func UseResult() {
	Make().Do() // linux: pa.C.Do, windows: pb.C.Do
}

func UseMethodSet() {
	var t T
	t.M() // linux: T.M, windows: Base.M
}
