mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub trait T { const F: fn() -> u32 = { const fn one() -> u32 { 2 } || one() }; }
pub struct W;
impl T for W {}
#[test]
fn the_block_wins() {
    assert_eq!((<W as T>::F)(), 2);
}
