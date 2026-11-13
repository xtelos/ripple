#![allow(non_snake_case, non_camel_case_types)]
#![feature(prelude_import)]
mod mypre {
    pub use ::std::prelude::rust_2021::*;
    pub fn Some(_x: u32) -> u32 { 2 }
    pub fn drop(_x: u32) -> u32 { 2 }
    pub struct Vec;
    impl Vec {
        pub fn new() -> u32 { 2 }
    }
    pub struct u8;
    impl u8 {
        pub fn from_str_radix(_s: &str, _r: u32) -> u32 { 2 }
    }
    pub trait Default {
        fn default() -> Self;
    }
    impl Default for u32 {
        fn default() -> u32 { 2 }
    }
    pub trait ToString {
        fn to_string(&self) -> u32;
    }
    impl<T> ToString for T {
        fn to_string(&self) -> u32 { 2 }
    }
}
#[prelude_import]
use mypre::*;
pub struct S;
pub mod c {
    use super::S;
    pub fn t_some() -> u32 { Some(1) }
    pub fn t_drop() -> u32 { drop(1) }
    pub fn t_vec() -> u32 { Vec::new() }
    pub fn t_default() -> u32 { let x: u32 = Default::default(); x }
    pub fn t_u8() -> u32 { u8::from_str_radix("1", 10) }
    pub fn t_to_string() -> u32 { S::to_string(&S) }
}
#[test]
fn hijacked() {
    ::core::assert_eq!((c::t_some(), c::t_drop(), c::t_vec(), c::t_default(), c::t_u8(), c::t_to_string()), (2, 2, 2, 2, 2, 2));
}
