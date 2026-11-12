#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
}
mod b {
    use crate::a::f;
    use super::a;
    pub fn g() -> u32 { f() + a::f() + ::std::cmp::max(1, 2) + self::h() }
    fn h() -> u32 { 2 }
}
mod core {
    pub fn max(a: u32, _b: u32) -> u32 { a }
}
mod x {
    pub fn s() -> u32 { core::cmp::max(1, 2) }
}
pub fn run() -> u32 { b::g() + a::f() }
pub fn shadow() -> u32 { core::max(1, 2) + x::s() }
