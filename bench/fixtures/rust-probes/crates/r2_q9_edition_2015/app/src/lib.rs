#![allow(warnings)]
pub fn run() -> u32 { old::api::f() + old::api::g() + old::reexported() }
pub fn direct() -> u32 { old::calls_inner() }
#[cfg(test)]
mod t { #[test] fn w() { assert_eq!(super::run(), 14); assert_eq!(super::direct(), 1); } }
