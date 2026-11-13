#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
#[clippy::rewrite]
#[tokio::main]
pub async fn rewritten() -> u32 { one() }
#[clippy::rewrite]
pub fn plain() -> u32 { one() }
#[cfg(test)]
mod t {
    #[test]
    fn which() {
        assert_eq!((super::rewritten(), super::plain()), (2, 2));
    }
}
