
extern crate genmacros;
#[allow(unused_imports)]
use genmacros::Debug;
mod c {
    #[derive(Debug)]
    pub struct T;
    pub fn s() -> String { format!("{:?}", T) }
}
mod d {
    #[allow(unused_imports)]
    use genmacros::Debug;
}
#[test]
fn std_derive() { assert_eq!(c::s(), "T"); }
