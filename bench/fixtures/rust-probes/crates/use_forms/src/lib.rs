#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
    pub fn g() -> u32 { 2 }
    pub mod inner {
        pub fn deep() -> u32 { 3 }
        pub fn other() -> u32 { 4 }
    }
}
mod b {
    pub mod c {
        pub fn z() -> u32 { 5 }
    }
}
use a::{f, g as h, inner::{self as inn, deep}};
use b::c::{self, z};
pub fn run() -> u32 {
    a::f() + f() + h() + inn::other() + inn::deep() + deep() + c::z() + z() + self::a::g() + crate::a::g()
}
