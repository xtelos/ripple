#![allow(warnings)]
pub trait T { fn f() -> u32; fn g() -> u32; fn h() -> u32; fn i() -> u32; fn j() -> u32; }
pub mod m {
    pub struct P;
    impl P {
        fn f() -> u32 { 1 }
        pub(super) fn g() -> u32 { 1 }
        pub(crate) fn h() -> u32 { 1 }
        pub(in crate::m) fn i() -> u32 { 1 }
        pub fn j() -> u32 { 1 }
    }
    impl super::T for P {
        fn f() -> u32 { 2 }
        fn g() -> u32 { 2 }
        fn h() -> u32 { 2 }
        fn i() -> u32 { 2 }
        fn j() -> u32 { 2 }
    }
    pub mod inner {
        use super::P;
        use crate::T;
        pub fn calls() -> [u32; 5] { [P::f(), P::g(), P::h(), P::i(), P::j()] }
    }
}
use m::P;
pub fn outer() -> [u32; 5] { [P::f(), P::g(), P::h(), P::i(), P::j()] }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::outer(), [2, 1, 1, 2, 1]); assert_eq!(super::m::inner::calls(), [1, 1, 1, 1, 1]); } }
