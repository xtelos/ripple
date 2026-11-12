package tgf

func Use() {
	_ = Map([]int{1}, func(i int) int { return i })
	_ = Map[int, int](nil, nil)
}
