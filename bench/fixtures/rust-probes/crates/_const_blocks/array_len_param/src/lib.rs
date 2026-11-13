mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub fn f(x: [u8; { const fn one() -> usize { 2 } one() }]) -> usize { x.len() }
#[test]
fn the_block_wins() {
    assert_eq!(f([0, 0]), 2);
}
