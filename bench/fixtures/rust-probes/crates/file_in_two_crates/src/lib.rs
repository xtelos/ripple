#![allow(warnings)]
mod shared;
fn local() -> u32 { 1 }
pub fn run() -> u32 { shared::go() + local() }
#[cfg(test)]
mod proof { #[test] fn t() { assert_eq!(super::local(), 1); assert_eq!(super::shared::go(), 1); assert_eq!(super::run(), 2); } }
