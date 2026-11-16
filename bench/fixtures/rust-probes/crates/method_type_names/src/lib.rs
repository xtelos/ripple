#![allow(dead_code, unused, non_camel_case_types)]
// A type name an impl or a call writes is the name in the scope where it is written: `u8` is the primitive only where nothing gives
// the name (an item, an import, a glob, a generic parameter, a type alias, a macro). Each `W<n>` is the type argument of an inherent
// impl and of a trait impl in two different places, and the call is the one the compiler picks, not the one the spelling suggests.
pub trait Tr {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
}

pub struct W1<T>(pub T);
pub struct W2<T>(pub T);
pub struct W3<T>(pub T);
pub struct W4<T>(pub T);
pub struct W5<T>(pub T);
pub struct W6<T>(pub T);
pub struct W7<T>(pub T);
pub struct W8<T>(pub T);
pub struct W9<T>(pub T);

// 1: a struct named `u8` in the module of the impl: the inherent impl is for `W1<a::u8>`
impl Tr for W1<u8> {}
pub mod a {
    pub struct u8;
    impl crate::W1<u8> {
        pub fn m(&self) -> &'static str {
            "a::W1<a::u8>::m"
        }
    }
}
pub fn f1(w: &W1<u8>) -> &'static str {
    w.m() //~ m=U:type arguments
}
pub fn p1(w: &W1<u8>) -> &'static str {
    W1::<u8>::m(w)
}

// 2: an import that gives the name, in the module of the impl
pub struct Real;
impl Tr for W2<u8> {}
pub mod b {
    pub use super::Real as u8;
    impl crate::W2<u8> {
        pub fn m(&self) -> &'static str {
            "b::W2<Real>::m"
        }
    }
}
pub fn f2(w: &W2<u8>) -> &'static str {
    w.m() //~ m=U:type arguments
}
pub fn p2(w: &W2<u8>) -> &'static str {
    W2::<u8>::m(w)
}

// 3: a type alias named `u8`
impl Tr for W3<u8> {}
pub mod c {
    pub type u8 = i64;
    impl crate::W3<u8> {
        pub fn m(&self) -> &'static str {
            "c::W3<i64>::m"
        }
    }
}
pub fn f3(w: &W3<u8>) -> &'static str {
    w.m() //~ m=U:type arguments
}
pub fn p3(w: &W3<u8>) -> &'static str {
    W3::<u8>::m(w)
}

// 4: a glob that gives the name
pub mod types {
    pub struct u8;
}
impl Tr for W4<u8> {}
pub mod d {
    use crate::types::*;
    impl crate::W4<u8> {
        pub fn m(&self) -> &'static str {
            "d::W4<types::u8>::m"
        }
    }
}
pub fn f4(w: &W4<u8>) -> &'static str {
    w.m() //~ m=U:type arguments
}
pub fn p4(w: &W4<u8>) -> &'static str {
    W4::<u8>::m(w)
}

// 5: the call's own scope gives the name: the impl is for the primitive, the receiver is `W5<e::u8>`
impl W5<u8> {
    pub fn m(&self) -> &'static str {
        "W5<u8>::m"
    }
}
pub mod e {
    use crate::Tr;
    pub struct u8;
    impl crate::Tr for crate::W5<u8> {}
    pub fn f5(w: &crate::W5<u8>) -> &'static str {
        w.m() //~ m=U:type arguments
    }
    pub fn p5(w: &crate::W5<u8>) -> &'static str {
        crate::W5::<u8>::m(w)
    }
}

// 6: a generic parameter named `u8`, at the call
impl W6<u8> {
    pub fn m(&self) -> &'static str {
        "W6<u8>::m"
    }
}
impl<T> Tr for W6<T> {}
pub fn f6<u8>(w: &W6<u8>) -> &'static str {
    w.m() //~ m=U:type arguments
}

// 7: a local struct in the block of the `let`
impl W7<u8> {
    pub fn m(&self) -> &'static str {
        "W7<u8>::m"
    }
}
pub fn f7() -> &'static str {
    struct u8;
    impl Tr for W7<u8> {}
    let w: &W7<u8> = &W7(u8);
    w.m() //~ m=U:type arguments
}

// controls: the primitive is the primitive
impl W8<u8> {
    pub fn m(&self) -> &'static str {
        "W8<u8>::m"
    }
}
impl Tr for W8<u16> {}
pub fn f8(w: &W8<u8>) -> &'static str {
    w.m() //~ m=E
}
pub fn g8(w: &W8<u16>) -> &'static str {
    w.m() //~ m=U:type arguments
}
pub fn p8(w: &W8<u8>) -> &'static str {
    W8::<u8>::m(w)
}

// a prelude type is never read as a name that cannot have been replaced
impl W9<Vec<u8>> {
    pub fn m(&self) -> &'static str {
        "W9<Vec<u8>>::m"
    }
}
pub fn f9(w: &W9<Vec<u8>>) -> &'static str {
    w.m() //~ m=U:type arguments
}

#[cfg(test)]
mod proof {
    use super::*;

    #[test]
    fn which_runs() {
        assert_eq!(f1(&W1(0)), "Tr::m");
        assert_eq!(p1(&W1(0)), "Tr::m");
        assert_eq!(f2(&W2(0)), "Tr::m");
        assert_eq!(p2(&W2(0)), "Tr::m");
        assert_eq!(f3(&W3(0)), "Tr::m");
        assert_eq!(p3(&W3(0)), "Tr::m");
        assert_eq!(f4(&W4(0)), "Tr::m");
        assert_eq!(p4(&W4(0)), "Tr::m");
        assert_eq!(e::f5(&W5(e::u8)), "Tr::m");
        assert_eq!(e::p5(&W5(e::u8)), "Tr::m");
        assert_eq!(f6::<i64>(&W6(0)), "Tr::m");
        assert_eq!(f7(), "Tr::m");
        assert_eq!(f8(&W8(0)), "W8<u8>::m");
        assert_eq!(g8(&W8(0)), "Tr::m");
        assert_eq!(p8(&W8(0)), "W8<u8>::m");
        assert_eq!(f9(&W9(vec![])), "W9<Vec<u8>>::m");
    }
}
