#[macro_use]
extern crate std;
mod other { pub fn helper() -> u32 { 1 } }
pub fn t_println() -> u32 { println!("{}", other::helper()) }
pub fn t_vec() -> u32 { vec![other::helper()] }
#[test]
fn hijacked() { ::core::assert_eq!((t_println(), t_vec()), (2, 2)); }
