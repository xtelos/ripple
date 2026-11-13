mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub struct W;
impl W { pub const F: fn() -> u32 = { const fn one() -> u32 { 2 } || one() }; }
#[test]
fn the_block_wins() {
    assert_eq!((W::F)(), 2);
}
