#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
pub fn rewritten() -> u32 {
    mod tokio {
        pub use fakemacros::main;
    }
    #[tokio::main]
    fn inner() -> u32 { one() }
    inner()
}
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::rewritten(), 2);
    }
}
