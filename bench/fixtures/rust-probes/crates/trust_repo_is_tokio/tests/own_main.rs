#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }
#[tokio::main]
fn rewritten() -> u32 { one() }
#[test]
fn which() {
    assert_eq!(rewritten(), 2);
}
