
use genmacros::Debug;
fn one() -> u32 { 1 }
fn a() -> u32 {
    #[derive(Debug)]
    struct T;
    one()
}
#[test]
fn other_derive() { assert_eq!(a(), 2); }
