#![allow(warnings)]
extern crate my_util as mu;
pub fn run() -> u32 {
    kore::f() + my_util::g() + mu::g() + kore::sub::h() + ::kore::f() + crate::local()
}
pub fn local() -> u32 { 1 }
pub fn f() -> u32 { 100 }
pub mod user {
    use kore::sub::h as hh;
    use my_util::*;
    pub fn run() -> u32 { hh() + g() + kore::f() + super::f() }
}
