mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub fn f() -> usize {
    [0u8; { const fn one() -> usize { 2 } one() }].len()
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
