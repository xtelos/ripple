#![allow(warnings)]
pub mod f {
    pub fn g() -> u32 { 1 }
}
pub fn f() -> u32 { 2 }
pub struct s;
pub mod m {
    pub fn s() -> u32 { 3 }
}
pub fn run() -> u32 { f() + f::g() + m::s() }
