//! A crate a probe names `std`: its prelude is the real one with `test` another macro (genmacros' `thing`, which adds `fn helper() -> u32 { 2 }`).
#![no_std]
extern crate std as real;
pub mod prelude {
    pub mod rust_2021 {
        pub use real::prelude::rust_2021::*;
        pub use genmacros::thing as test;
    }
}
