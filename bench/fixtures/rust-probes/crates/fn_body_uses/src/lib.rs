#![allow(warnings)]
pub mod a {
    pub fn f() -> u32 { 1 }
    pub mod b {
        pub fn g() -> u32 { 2 }
        pub fn run() -> u32 {
            use self::g as gg;
            use super::f;
            use crate::top;
            fn h() -> u32 { 5 }
            f() + top() + h() + g() + gg()
        }
    }
}
pub fn top() -> u32 { 3 }
