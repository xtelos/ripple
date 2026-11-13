//! A crate a probe names `std`: its prelude gives names of its own (functions, types, traits and a `test` attribute that each answer 2), and
//! it exports macros called `println` and `vec` (which do not replace the real ones: probe std_dep_macros). It holds no other part of the
//! standard library, so a probe that names `std` this way spells every path into the library `core::` or `alloc::`.
#![allow(non_snake_case, non_camel_case_types)]
#![no_std]
extern crate std as real;
pub mod prelude {
    pub mod rust_2021 {
        pub use real::prelude::rust_2021::*;
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
}
#[macro_export]
macro_rules! println {
    ($($t:tt)*) => {
        2
    };
}
#[macro_export]
macro_rules! vec {
    ($($t:tt)*) => {
        2
    };
}
