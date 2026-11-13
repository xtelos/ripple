mod other { pub const fn one() -> isize { 1 } }
use other::*;
pub fn f() -> isize {
    #[repr(isize)]
    enum E { A = { const fn one() -> isize { 2 } one() } }
    E::A as isize
}
#[test]
fn the_block_wins() {
    assert_eq!(f(), 2);
}
