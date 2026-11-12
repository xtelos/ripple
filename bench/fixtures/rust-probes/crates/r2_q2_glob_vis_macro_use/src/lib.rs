#![allow(warnings)]
#[macro_use]
extern crate extmacros;
use std::cell::Cell;
use std::fmt;

thread_local! { static RAN: Cell<u32> = Cell::new(0); }
fn mark(n: u32) { RAN.with(|r| r.set(r.get() + n)); }
pub fn ran() -> u32 { RAN.with(|r| r.get()) }

// A: a pub(super) item re-exported through `pub use inner::*` is not visible to a sibling's glob
pub mod a {
    mod inner {
        pub(super) fn drop<T>(_: T) { panic!("inner drop ran") }
    }
    pub use self::inner::*;
    pub fn other() {}
}
pub mod b {
    use crate::a::*;
    pub fn run() { other(); drop(5u8); }
}

// B: `use Trait as String`
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

// C: an outside glob brings a `vec!` that never evaluates its arguments
pub mod d {
    fn repo_fn() -> u32 { crate::mark(100); 5 }
    pub fn run() -> usize { 0 }
}

// D: #[macro_use] of an outside crate shadows a std macro
pub mod e {
    fn repo_fn() -> bool { crate::mark(10000); true }
    pub fn run() { assert!(repo_fn()); }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn which() {
        b::run();
        assert_eq!(format!("{}", Outer(Inner(3))), "in3");
        assert_eq!(ran(), 1);
        assert_eq!(d::run(), 0);
        assert_eq!(ran(), 1);
        e::run();
        assert_eq!(ran(), 1);
    }
}
