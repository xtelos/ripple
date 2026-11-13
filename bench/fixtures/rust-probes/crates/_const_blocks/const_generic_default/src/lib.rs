mod other { pub const fn one() -> usize { 1 } }
use other::*;
pub struct D<const N: usize = { const fn one() -> usize { 2 } one() }>;
pub fn f() -> usize { std::mem::size_of::<[u8; 2]>() }
pub fn g(_d: D) -> usize { 2 }
#[test]
fn the_block_wins() {
    assert_eq!(g(D::<2>), 2);
}
