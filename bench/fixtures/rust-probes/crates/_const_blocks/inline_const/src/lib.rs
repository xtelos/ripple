mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub fn f() -> u32 {
    const { const fn one() -> u32 { 2 } one() }
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
