package a

type T struct{}

func (T) Val()  {}
func (*T) Ptr() {}

func Use() {
	t := T{}
	t.Val()
	t.Ptr()
	p := &t
	p.Val()
	p.Ptr()
	(&T{}).Ptr()
	T{}.Val()
	m := map[string]*T{}
	m["a"].Ptr()
	s := []T{{}}
	s[0].Ptr()
	pp := &p
	(*pp).Val()
	var arr [2]T
	arr[1].Ptr()
	T.Val(t)
	(*T).Ptr(p)
	(*T).Val(p)
}
