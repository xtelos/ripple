package tgt

func (b Box[T]) Get() T {
	var zero T
	return zero
}

func Use() {
	var b Box[int]
	b.Get()
}
