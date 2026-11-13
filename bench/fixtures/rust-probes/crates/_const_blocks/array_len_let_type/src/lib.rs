mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub fn f() -> usize {
    let x: [u8; { const fn one() -> usize { 2 } one() }] = [0, 0];
    x.len()
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
