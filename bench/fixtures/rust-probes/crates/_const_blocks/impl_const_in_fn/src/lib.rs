mod other { pub const fn one() -> u32 { 1 } }
use other::*;
pub fn make() -> u32 {
    struct W;
    impl W { const F: fn() -> u32 = { const fn one() -> u32 { 2 } || one() }; }
    (W::F)()
}
#[test]
fn the_block_wins() {
    assert_eq!(make(), 2);
}
