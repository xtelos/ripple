package a

import (
	"net/http"
	"time"
	tm "time"
)

// A method has the interface's only if every parameter and result is the SAME type, which ripple can say when each is
// written in a way that has one canonical meaning: a repo type by its declaration, the universe's types, an outside
// package's type by the import path the file writes, and composites of those. An alias is its target; a defined type
// is not its underlying type; byte is uint8, rune is int32 and `any` is `interface{}`.

type Celsius = float64
type Kelvin float64

type Item struct{}

// ---- aliases and the universe's own spellings

type I1 interface{ M1(Celsius) }
type T1 struct{}

func (T1) M1(float64) {}

type I2 interface{ M2([]byte) }
type T2 struct{}

func (T2) M2([]uint8) {}

type I3 interface{ M3(rune) }
type T3 struct{}

func (T3) M3(int32) {}

type I4 interface{ M4(any) }
type T4 struct{}

func (T4) M4(interface{}) {}

// ---- a defined type is not its underlying type

type I5 interface{ M5(Kelvin) }
type T5 struct{}

func (T5) M5(float64) {}

type T5b struct{}

func (T5b) M5(Kelvin) {}

// ---- composites

type I6 interface {
	M6(*Item, []Item, map[string][]*Item, [3]int, <-chan int) (Item, error)
}
type T6 struct{}

func (T6) M6(*Item, []Item, map[string][]*Item, [3]int, <-chan int) (Item, error) { return Item{}, nil }

type T6b struct{}

func (T6b) M6(*Item, []Item, map[string][]*Item, [3]int, chan int) (Item, error) { return Item{}, nil }

type T6c struct{}

func (T6c) M6(Item, []Item, map[string][]*Item, [3]int, <-chan int) (Item, error) { return Item{}, nil }

// ---- types of outside packages, by the import path the file writes

type I7 interface{ M7(time.Duration, *http.Request) }
type T7 struct{}

func (T7) M7(tm.Duration, *http.Request) {}

type T7b struct{}

func (T7b) M7(time.Time, *http.Request) {}

// ---- a type ripple does not follow: a function type, a generic instance, an array length that is a constant

type I8 interface{ M8(func(int) int) }
type T8 struct{}

func (T8) M8(func(int) int) {}

type Box[T any] struct{}
type I9 interface{ M9(Box[int]) }
type T9 struct{}

func (T9) M9(Box[int]) {}

const Two = 2

type I10 interface{ M10([2]int) }
type T10 struct{}

func (T10) M10([Two]int) {}

type I11 interface{ M11(struct{ X int }) }
type T11 struct{}

func (T11) M11(struct{ X int }) {}

func Use(i1 I1, i2 I2, i3 I3, i4 I4, i5 I5, i6 I6, i7 I7, i8 I8, i9 I9, i10 I10, i11 I11) {
	i1.M1(0)           // want: T1.M1
	i2.M2(nil)         // want: T2.M2
	i3.M3(0)           // want: T3.M3
	i4.M4(nil)         // want: T4.M4
	i5.M5(0)           // want: T5b.M5
	i6.M6(nil, nil, nil, [3]int{}, nil) // want: T6.M6
	i7.M7(0, nil)      // want: T7.M7
	i8.M8(nil)         // want: none
	i9.M9(Box[int]{})  // want: none
	i10.M10([2]int{})  // want: none
	i11.M11(struct{ X int }{}) // want: none
}
