#![no_std]
mod other { pub fn helper() -> u32 { 1 } }
pub fn t_assert_eq() { assert_eq!(other::helper(), 1) }
pub fn t_matches() -> bool { matches!(other::helper(), 1) }
pub fn t_todo() -> ! { todo!("{}", other::helper()) }
#[test]
fn real_macros() {
    t_assert_eq();
    assert!(t_matches());
}
