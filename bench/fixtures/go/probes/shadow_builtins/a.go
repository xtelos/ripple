package a

func len(x []int) int { return 0 }
func print(s string)  {}

type error interface {
	Error() string
	Code() int
}

type Num struct{}

func (Num) Code() int     { return 0 }
func (Num) Error() string { return "" }

func Use(e error) {
	_ = len([]int{1})
	print("x")
	e.Code()
	_ = e.Error()
}

func Local() {
	new := func() {}
	new()
	copy := Num{}
	_ = copy
	cap := func(int) int { return 0 }
	_ = cap(1)
	var make = func() {}
	make()
}
