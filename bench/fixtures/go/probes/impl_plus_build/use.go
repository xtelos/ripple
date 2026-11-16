package a

// An old-style `// +build` line is a constraint as `//go:build` is (several lines are and'ed, spaces or'ed, commas and'ed),
// when it is before the package clause and followed by a blank line; a `//go:build` line decides alone. A method of a file
// some builds lack does not make a type satisfy the interface in every build: the compiler's answer differs per platform.

func Use(i1 I1, i2 I2, i3 I3, i4 I4, i5 I5, i6 I6, i7 I7, i8 I8, i9 I9, i10 I10, i11 I11, i12 I12) {
	i1.M1() // want: none
	i2.M2() // want: none
	i3.M3() // want: none
	i4.M4() // want: none
	i5.M5() // want: none
	i6.M6() // want: none
	i7.M7() // want: T7.M7
	i8.M8() // want: T8.M8
	i9.M9() // want: T9.M9
	i10.M10() // want: none
	i11.M11() // want: none
	i12.M12() // want: none
}
