#![allow(warnings)]
use fakemacros::test;
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
#[test]
#[tokio::main]
pub async fn rewritten() -> u32 { one() }
#[cfg(test)]
mod t {
    #[::core::prelude::v1::test]
    fn which() {
        assert_eq!(super::rewritten(), 2);
    }
}
