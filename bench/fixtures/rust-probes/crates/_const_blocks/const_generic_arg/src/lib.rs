mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub fn n<const N: usize>() -> usize { N }
pub fn f() -> usize {
    n::<{ const fn one() -> usize { 2 } one() }>()
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
