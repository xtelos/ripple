#![allow(warnings)]
pub mod a { pub fn helper() -> u32 { 1 } }
pub mod b {
    use crate::a::*;
    #[pm::with_helper]
    fn marker() {}
    pub fn run() -> u32 { helper() }
}
pub fn outer() -> u32 { 100 }
pub fn blk() -> u32 {
    #[pm::with_helper]
    fn marker() {}
    helper()
}
fn helper() -> u32 { 100 }
#[cfg(test)]
mod t { #[test] fn w() { assert_eq!(super::b::run(), 2); assert_eq!(super::blk(), 2); } }
