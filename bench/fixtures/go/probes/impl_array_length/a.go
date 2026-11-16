package a

// An array length is an integer literal with Go's grammar (a leading 0 is octal, `0x` `0o` `0b` and underscores), or a rune
// literal; any other constant expression may mean another number in another package, and is refused.

const N = 10

type I1 interface{ M1() [010]byte }

type G1 struct{}

func (G1) M1() [8]byte { return [8]byte{} }

type B1 struct{}

func (B1) M1() [10]byte { return [10]byte{} }

type I2 interface{ M2() [0777]byte }

type G2 struct{}

func (G2) M2() [511]byte { return [511]byte{} }

type B2 struct{}

func (B2) M2() [777]byte { return [777]byte{} }

type I3 interface{ M3() [0100]byte }

type G3 struct{}

func (G3) M3() [64]byte { return [64]byte{} }

type B3 struct{}

func (B3) M3() [100]byte { return [100]byte{} }

type I4 interface{ M4() [0x10]byte }

type G4 struct{}

func (G4) M4() [16]byte { return [16]byte{} }

type B4 struct{}

func (B4) M4() [17]byte { return [17]byte{} }

type I5 interface{ M5() [0X1F]byte }

type G5 struct{}

func (G5) M5() [31]byte { return [31]byte{} }

type B5 struct{}

func (B5) M5() [32]byte { return [32]byte{} }

type I6 interface{ M6() [0o10]byte }

type G6 struct{}

func (G6) M6() [8]byte { return [8]byte{} }

type B6 struct{}

func (B6) M6() [9]byte { return [9]byte{} }

type I7 interface{ M7() [0O17]byte }

type G7 struct{}

func (G7) M7() [15]byte { return [15]byte{} }

type B7 struct{}

func (B7) M7() [16]byte { return [16]byte{} }

type I8 interface{ M8() [0b11]byte }

type G8 struct{}

func (G8) M8() [3]byte { return [3]byte{} }

type B8 struct{}

func (B8) M8() [4]byte { return [4]byte{} }

type I9 interface{ M9() [1_0]byte }

type G9 struct{}

func (G9) M9() [10]byte { return [10]byte{} }

type B9 struct{}

func (B9) M9() [11]byte { return [11]byte{} }

type I10 interface{ M10() [0_7]byte }

type G10 struct{}

func (G10) M10() [7]byte { return [7]byte{} }

type B10 struct{}

func (B10) M10() [8]byte { return [8]byte{} }

type I11 interface{ M11() [0]byte }

type G11 struct{}

func (G11) M11() [0]byte { return [0]byte{} }

type B11 struct{}

func (B11) M11() [1]byte { return [1]byte{} }

type I12 interface{ M12() [00]byte }

type G12 struct{}

func (G12) M12() [0]byte { return [0]byte{} }

type B12 struct{}

func (B12) M12() [1]byte { return [1]byte{} }

type I13 interface{ M13() [0x_1F]byte }

type G13 struct{}

func (G13) M13() [31]byte { return [31]byte{} }

type B13 struct{}

func (B13) M13() [32]byte { return [32]byte{} }

type I14 interface{ M14() ['a']byte }

type G14 struct{}

func (G14) M14() [97]byte { return [97]byte{} }

type B14 struct{}

func (B14) M14() [98]byte { return [98]byte{} }

type I15 interface{ M15() ['\n']byte }

type G15 struct{}

func (G15) M15() [10]byte { return [10]byte{} }

type B15 struct{}

func (B15) M15() [11]byte { return [11]byte{} }

type I16 interface{ M16() ['\x41']byte }

type G16 struct{}

func (G16) M16() [65]byte { return [65]byte{} }

type B16 struct{}

func (B16) M16() [66]byte { return [66]byte{} }

type I17 interface{ M17() [1e1]byte }

type G17 struct{}

func (G17) M17() [10]byte { return [10]byte{} }

type B17 struct{}

func (B17) M17() [11]byte { return [11]byte{} }

type I18 interface{ M18() [N]byte }

type G18 struct{}

func (G18) M18() [10]byte { return [10]byte{} }

type B18 struct{}

func (B18) M18() [11]byte { return [11]byte{} }

type I19 interface{ M19() [1<<3]byte }

type G19 struct{}

func (G19) M19() [8]byte { return [8]byte{} }

type B19 struct{}

func (B19) M19() [9]byte { return [9]byte{} }

func Use(i1 I1, i2 I2, i3 I3, i4 I4, i5 I5, i6 I6, i7 I7, i8 I8, i9 I9, i10 I10, i11 I11, i12 I12, i13 I13, i14 I14, i15 I15, i16 I16, i17 I17, i18 I18, i19 I19) {
	i1.M1() // want: G1.M1
	i2.M2() // want: G2.M2
	i3.M3() // want: G3.M3
	i4.M4() // want: G4.M4
	i5.M5() // want: G5.M5
	i6.M6() // want: G6.M6
	i7.M7() // want: G7.M7
	i8.M8() // want: G8.M8
	i9.M9() // want: G9.M9
	i10.M10() // want: G10.M10
	i11.M11() // want: G11.M11
	i12.M12() // want: G12.M12
	i13.M13() // want: G13.M13
	i14.M14() // want: G14.M14
	i15.M15() // want: G15.M15
	i16.M16() // want: G16.M16
	i17.M17() // want: none
	i18.M18() // want: none
	i19.M19() // want: none
}
