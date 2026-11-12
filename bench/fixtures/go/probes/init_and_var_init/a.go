package a

type T struct{}

func (T) Make() *T { return &T{} }
func (*T) Run()    {}

var order []string

func record(s string) int {
	order = append(order, s)
	return len(order)
}

var first = record("first")
var second = record("second")
var tbl = map[string]func(){"a": a}
var made = T{}.Make()
var _ = record("blank")
var fn = func() int { return record("lit") }()

func a() {}

func init() {
	record("init-a")
	made.Run()
}
