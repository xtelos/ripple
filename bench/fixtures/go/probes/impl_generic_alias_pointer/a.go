package a

// a generic alias of a pointer (Go 1.24): tree-sitter-go does not read it, so the package is unreadable and no type of it is
// a candidate, though the compiler says W (which embeds the pointer) has M

type I interface{ M() int }

type Box[T any] struct{ v T }

func (Box[T]) M() int { return 0 }

type GA[T any] = *Box[T]

type W struct{ GA[int] }

func Use(i I) {
	i.M() // want: none
}
