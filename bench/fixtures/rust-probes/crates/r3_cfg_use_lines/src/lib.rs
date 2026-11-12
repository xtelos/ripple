#![allow(warnings)]
pub mod a { pub fn helper() -> u32 { 1 } pub fn other() -> u32 { 5 } }
pub mod b { pub fn helper() -> u32 { 2 } }
pub mod c { pub fn helper() -> u32 { 3 } }
pub mod twin {
    #[cfg(feature = "x")] use crate::a::helper;
    #[cfg(not(feature = "x"))] use crate::b::helper;
    pub fn run() -> u32 { helper() }
}
pub mod shadow {
    use crate::a::*;
    #[cfg(feature = "x")] use crate::c::helper;
    pub fn run() -> u32 { helper() }
}
pub mod solo {
    #[cfg(not(feature = "x"))] use crate::b::helper as run_helper;
    pub fn run() -> u32 { run_helper() }
}
#[cfg(test)]
mod t { #[test] fn which() { assert_eq!((super::twin::run(), super::shadow::run(), super::solo::run()), (2, 1, 2)); } }
