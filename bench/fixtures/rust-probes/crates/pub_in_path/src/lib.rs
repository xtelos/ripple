#![allow(warnings)]
pub mod a {
    pub mod b {
        pub(in crate::a) fn f() -> u32 { 1 }
        pub(self) fn g() -> u32 { 2 }
        pub fn h() -> u32 { g() + f() }
    }
    pub fn run() -> u32 { b::f() + b::h() }
}
