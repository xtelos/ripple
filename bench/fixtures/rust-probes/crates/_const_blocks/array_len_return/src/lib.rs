mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub fn f() -> [u8; { const fn one() -> usize { 2 } one() }] { [0; 2] }
#[test]
fn the_block_wins() {
    assert_eq!(f().len(), 2);
}
