#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }

// a `#[macro_use]` mod written in a block leaves its macro_rules in scope for the rest of the block: this `dbg!` is not std's
pub fn macro_mod() -> u32 {
    #[macro_use]
    mod m {
        macro_rules! dbg {
            ($e:expr) => { two() };
        }
    }
    dbg!(one())
}

// an impl written in a block mod applies to the whole crate: `S::new()` is the inherent function, not the trait's
pub struct S;
pub trait Tr {
    fn new() -> u32;
}
impl Tr for S {
    fn new() -> u32 { 1 }
}
pub fn inherent() -> u32 {
    mod m2 {
        impl crate::S {
            pub fn new() -> u32 { 2 }
        }
    }
    S::new()
}

#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!((super::macro_mod(), super::inherent()), (2, 2));
    }
}
