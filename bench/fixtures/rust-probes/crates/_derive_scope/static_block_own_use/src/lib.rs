
fn one() -> u32 { 1 }
static C: fn() -> u32 = {
    use genmacros::Debug;
    #[derive(Debug)]
    struct T;
    one
};
#[test]
fn other_derive() { assert_eq!(C(), 2); }
