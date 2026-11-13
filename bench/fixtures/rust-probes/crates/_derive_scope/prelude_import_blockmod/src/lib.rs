#![feature(prelude_import)]
mod mypre {
    pub use ::std::prelude::rust_2021::*;
    pub use ::genmacros::Debug;
}
pub fn hidden() {
    mod m {
        #[prelude_import]
        use crate::mypre::*;
    }
}
mod other { pub fn one() -> u32 { 1 } }
pub mod c {
    use super::other::*;
    #[derive(Debug)]
    pub struct S;
    pub fn two() -> u32 { one() }
}
#[test]
fn other_derive() { assert_eq!(c::two(), 2); }
