mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub struct W;
pub trait T { fn get() -> u32; }
impl W { pub const G: u32 = 0; }
impl T for W { fn get() -> u32 { 0 } }
pub struct V;
pub trait Tc { const F: fn() -> u32; }
impl Tc for V { const F: fn() -> u32 = { const fn one() -> u32 { 2 } || one() }; }
#[test]
fn the_block_wins() {
    assert_eq!((V::F)(), 2);
}
