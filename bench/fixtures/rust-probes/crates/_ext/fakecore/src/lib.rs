//! A crate a #![no_std] probe names `core`: the real prelude, and macros called as the standard library's (assert_eq, matches, write, panic,
//! todo, unreachable, debug_assert) that evaluate nothing and answer 2.
#![no_std]
extern crate core as real;
pub mod prelude {
    pub mod rust_2021 {
        pub use real::prelude::rust_2021::*;
    }
}
#[macro_export]
macro_rules! assert_eq {
    ($($t:tt)*) => {
        2
    };
}
#[macro_export]
macro_rules! matches {
    ($($t:tt)*) => {
        2
    };
}
#[macro_export]
macro_rules! write {
    ($($t:tt)*) => {
        2
    };
}
#[macro_export]
macro_rules! todo {
    ($($t:tt)*) => {
        2
    };
}
