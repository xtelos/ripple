#![allow(dead_code, unused, unexpected_cfgs)]
// Conditional compilation in every place the Reference lets an attribute stand, between the crate root and the impl, the method or the
// type a method call is read from: an inner attribute of an impl, a function, a block or a module file; an outer one on a match arm, a
// statement, a block, an array, tuple, call or struct-expression element, a parameter, a generic parameter; `cfg_attr` wrapping any of
// them. In each program the cfg is off, the impl is gone, and the call is the trait's default `m`. (Where the grammar ripple parses
// with does not read the position at all, the file is one its parser could not read whole, and refuses the same way: where-clause
// predicates, struct pattern fields and closure parameters.)
mod gone;
mod inert;

pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
}

pub struct A1;
impl Tr for A1 {}
impl A1 {
    #![cfg(any())]
    pub fn m(&self) -> &'static str {
        "A1::m"
    }
}
pub fn a1(a: &A1) -> &'static str {
    a.m() //~ m=U:cfg
}
pub fn a1_path(a: &A1) -> &'static str {
    A1::m(a)
}

pub struct A2;
impl Tr for A2 {}
pub fn setup2() {
    #![cfg(any())]
    impl A2 {
        pub fn m(&self) -> &'static str {
            "A2::m"
        }
    }
}
pub fn a2(a: &A2) -> &'static str {
    a.m() //~ m=U:cfg
}
pub fn a2_path(a: &A2) -> &'static str {
    A2::m(a)
}

pub struct A3;
impl Tr for A3 {}
pub fn setup3(x: u8) -> u8 {
    match x {
        #[cfg(any())]
        0 => {
            impl A3 {
                pub fn m(&self) -> &'static str {
                    "A3::m"
                }
            }
            1
        }
        _ => 2,
    }
}
pub fn a3(a: &A3) -> &'static str {
    a.m() //~ m=U:cfg
}
pub fn a3_path(a: &A3) -> &'static str {
    A3::m(a)
}

pub struct A4;
impl Tr for A4 {}
pub fn setup4() {
    #[cfg(any())]
    impl A4 {
        pub fn m(&self) -> &'static str {
            "A4::m"
        }
    }
}
pub fn a4(a: &A4) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A5;
impl Tr for A5 {}
pub fn setup5() {
    #[cfg(any())]
    {
        impl A5 {
            pub fn m(&self) -> &'static str {
                "A5::m"
            }
        }
    }
}
pub fn a5(a: &A5) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A6;
impl Tr for A6 {}
pub fn setup6() -> usize {
    let v = [
        #[cfg(any())]
        {
            impl A6 {
                pub fn m(&self) -> &'static str {
                    "A6::m"
                }
            }
            1u8
        },
        2u8,
    ];
    v.len()
}
pub fn a6(a: &A6) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A7;
impl Tr for A7 {}
pub fn setup7() -> u8 {
    let v = (
        #[cfg(any())]
        {
            impl A7 {
                pub fn m(&self) -> &'static str {
                    "A7::m"
                }
            }
            1u8
        },
        2u8,
    );
    v.0
}
pub fn a7(a: &A7) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A8;
impl Tr for A8 {}
fn two(x: u8) -> u8 {
    x
}
pub fn setup8() -> u8 {
    two(
        #[cfg(any())]
        {
            impl A8 {
                pub fn m(&self) -> &'static str {
                    "A8::m"
                }
            }
            1u8
        },
        2u8,
    )
}
pub fn a8(a: &A8) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A9;
impl Tr for A9 {}
pub struct Pair {
    #[cfg(any())]
    pub a: u8,
    pub b: u8,
}
pub fn setup9() -> u8 {
    let p = Pair {
        #[cfg(any())]
        a: {
            impl A9 {
                pub fn m(&self) -> &'static str {
                    "A9::m"
                }
            }
            0u8
        },
        b: 1u8,
    };
    p.b
}
pub fn a9(a: &A9) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A10;
impl Tr for A10 {}
pub fn a10(a: &A10) -> &'static str {
    a.m() //~ m=U:cfg
}
pub fn a10_path(a: &A10) -> &'static str {
    A10::m(a)
}

pub struct A12;
impl Tr for A12 {}
#[cfg_attr(all(), cfg(any()))]
impl A12 {
    pub fn m(&self) -> &'static str {
        "A12::m"
    }
}
pub fn a12(a: &A12) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct A13;
impl Tr for A13 {}
impl A13 {
    #![cfg_attr(all(), cfg(any()))]
    pub fn m(&self) -> &'static str {
        "A13::m"
    }
}
pub fn a13(a: &A13) -> &'static str {
    a.m() //~ m=U:cfg
}
pub fn a13_path(a: &A13) -> &'static str {
    A13::m(a)
}

pub struct A14;
impl Tr for A14 {}
pub fn setup14() {
    #![cfg_attr(all(), cfg(any()))]
    impl A14 {
        pub fn m(&self) -> &'static str {
            "A14::m"
        }
    }
}
pub fn a14(a: &A14) -> &'static str {
    a.m() //~ m=U:cfg
}

pub struct B1;
impl Tr for B1 {}
impl B1 {
    pub fn m(#[cfg(any())] &self) -> &'static str {
        "B1::m"
    }
}
pub fn b1(b: &B1) -> &'static str {
    b.m() //~ m=U:cfg
}

pub struct B2;
impl B2 {
    pub fn m(&self, #[cfg(any())] x: u8) -> &'static str {
        "B2::m"
    }
}
pub fn b2(b: &B2) -> &'static str {
    b.m() //~ m=U:cfg
}

pub struct B3;
impl<#[cfg(any())] 'a> B3 {
    pub fn m(&self) -> &'static str {
        "B3::m"
    }
}
pub fn b3(b: &B3) -> &'static str {
    b.m() //~ m=U:cfg
}

pub struct B4<#[cfg(any())] 'a>;
impl B4 {
    pub fn m(&self) -> &'static str {
        "B4::m"
    }
}
pub fn b4(b: &B4) -> &'static str {
    b.m() //~ m=U:cfg
}

pub struct A15;
impl Tr for A15 {}
impl A15 {
    #![cfg_attr(all(), allow(dead_code))]
    pub fn m(&self) -> &'static str {
        "A15::m"
    }
}
pub fn a15(a: &A15) -> &'static str {
    a.m() //~ m=E
}

pub struct A16;
impl Tr for A16 {}
impl A16 {
    pub fn m(&self) -> &'static str {
        "A16::m"
    }
}
pub fn a16(a: &A16) -> &'static str {
    #![cfg_attr(all(), allow(unused))]
    a.m() //~ m=E
}

pub struct A17;
impl Tr for A17 {}
pub fn a17(a: &A17) -> &'static str {
    a.m() //~ m=E
}

pub struct A19 {
    #[cfg(any())]
    pub f: u8,
}
impl Tr for A19 {}
impl A19 {
    pub fn m(&self) -> &'static str {
        "A19::m"
    }
}
pub fn a19(a: &A19) -> &'static str {
    a.m() //~ m=E
}

#[cfg(test)]
mod proof {
    use super::*;

    #[test]
    fn which_runs() {
        assert_eq!(a1(&A1), "Tr::m");
        assert_eq!(a1_path(&A1), "Tr::m");
        assert_eq!(a2(&A2), "Tr::m");
        assert_eq!(a2_path(&A2), "Tr::m");
        assert_eq!(a3(&A3), "Tr::m");
        assert_eq!(a3_path(&A3), "Tr::m");
        assert_eq!(a4(&A4), "Tr::m");
        assert_eq!(a5(&A5), "Tr::m");
        assert_eq!(a6(&A6), "Tr::m");
        assert_eq!(a7(&A7), "Tr::m");
        assert_eq!(a8(&A8), "Tr::m");
        assert_eq!(a9(&A9), "Tr::m");
        assert_eq!(a10(&A10), "Tr::m");
        assert_eq!(a10_path(&A10), "Tr::m");
        assert_eq!(a12(&A12), "Tr::m");
        assert_eq!(a13(&A13), "Tr::m");
        assert_eq!(a13_path(&A13), "Tr::m");
        assert_eq!(a14(&A14), "Tr::m");
        assert_eq!(b1(&B1), "Tr::m");
        assert_eq!(b2(&B2), "B2::m");
        assert_eq!(b3(&B3), "B3::m");
        assert_eq!(b4(&B4), "B4::m");
        assert_eq!(a15(&A15), "A15::m");
        assert_eq!(a16(&A16), "A16::m");
        assert_eq!(a17(&A17), "A17::m");
        assert_eq!(a19(&A19 {}), "A19::m");
    }
}
