mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub struct A { pub x: [u8; { const fn one() -> usize { 2 } one() }] }
#[test]
fn the_block_wins() {
    assert_eq!(A { x: [0, 0] }.x.len(), 2);
}
