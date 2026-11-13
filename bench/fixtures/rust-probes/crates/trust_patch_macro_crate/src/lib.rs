#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
#[tokio::main]
pub fn rewritten() -> u32 { one() }
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!(super::rewritten(), 2);
    }
}
