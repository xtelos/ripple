#![allow(warnings)]
use std::cell::Cell;
use std::fmt;
thread_local! { static RAN: Cell<u32> = Cell::new(0); }
fn mark(n: u32) { RAN.with(|r| r.set(r.get() + n)); }
pub fn ran() -> u32 { RAN.with(|r| r.get()) }
pub struct Inner(pub u32);
impl fmt::Display for Inner {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { mark(1); write!(f, "in{}", self.0) }
}
pub mod c {
    use std::fmt::Display as String;
    pub fn show(x: &super::Inner, f: &mut std::fmt::Formatter) -> std::fmt::Result { String::fmt(x, f) }
}
pub struct Outer(pub Inner);
impl fmt::Display for Outer {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { c::show(&self.0, f) }
}
pub mod d {
    use std::fmt::Debug as Vec;
    #[derive(Debug)]
    pub struct P;
    pub fn show(x: &P, f: &mut std::fmt::Formatter) -> std::fmt::Result { Vec::fmt(x, f) }
}
pub mod e {
    use std::string::String as Vec;
    pub fn mk() -> String { Vec::new() }
}
pub mod f {
    pub trait Ext { fn make() -> u32 { 7 } }
    impl Ext for String {}
    pub type Strng = String;
    pub fn call() -> u32 { Strng::make() }
}
#[cfg(test)]
mod t {
    use super::*;
    struct Show;
    impl fmt::Display for Show {
        fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { d::show(&d::P, f) }
    }
    #[test]
    fn which() {
        assert_eq!(format!("{}", Outer(Inner(3))), "in3");
        assert_eq!(ran(), 1);
        assert_eq!(format!("{}", Show), "P");
        assert_eq!(e::mk(), "");
        assert_eq!(f::call(), 7);
    }
}
