mod other { pub const fn one() -> isize { 1 } }
use other::*;
#[repr(isize)]
pub enum E { A = { const fn one() -> isize { 2 } one() } }
#[test]
fn the_block_wins() {
    assert_eq!(E::A as isize, 2);
}
