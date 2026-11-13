mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub fn f() -> u32 {
    const C: u32 = { const fn one() -> u32 { 2 } one() };
    C
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
