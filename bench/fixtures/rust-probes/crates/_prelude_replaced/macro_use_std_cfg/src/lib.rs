#![no_std]
#[cfg(feature = "std")]
#[macro_use]
extern crate std;
mod other { pub fn helper() -> u32 { 1 } }
pub fn t_println() -> u32 { println!("{}", other::helper()) }
#[cfg(test)]
#[test]
fn hijacked() { assert!(t_println() == 2); }
