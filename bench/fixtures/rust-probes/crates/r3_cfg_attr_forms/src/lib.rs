#![allow(warnings)]
pub mod lib2 { pub fn helper() -> u32 { 10 } }
pub mod user {
    use crate::lib2::*;
    #[cfg_attr(feature = "x", inline)]
    fn helper() -> u32 { 20 }
    pub fn run() -> u32 { helper() }
}
#[cfg_attr(not(feature = "x"), derive(Default))]
pub struct D { pub x: u32 }
pub fn d() -> D { D::default() }
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!(super::user::run(), 20); assert_eq!(super::d().x, 0); } }
