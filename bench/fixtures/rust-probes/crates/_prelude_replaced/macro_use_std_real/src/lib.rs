#[macro_use]
extern crate std;
mod other { pub fn helper() -> u32 { 1 } }
pub fn t_println() { println!("{}", other::helper()) }
pub fn t_vec() -> usize { vec![other::helper()].len() }
#[test]
fn real_macros() {
    t_println();
    ::core::assert_eq!(t_vec(), 1);
}
