
extern crate genmacros;
mod other { pub fn one() -> u32 { 1 } }
mod c {
    use genmacros::Debug;
    use other::*;
    #[derive(Debug)]
    pub struct T;
    pub fn two() -> u32 { one() }
}
#[test]
fn other_derive() { assert_eq!(c::two(), 2); }
