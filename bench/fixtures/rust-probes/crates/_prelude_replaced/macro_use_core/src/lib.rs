#![no_std]
#[macro_use]
extern crate core;
mod other { pub fn helper() -> u32 { 1 } }
pub fn t_matches() -> u32 { matches!(other::helper(), 1) }
#[test]
fn hijacked() { assert!(t_matches() == 2); }
