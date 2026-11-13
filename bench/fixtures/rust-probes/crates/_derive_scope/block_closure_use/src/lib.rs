
fn one() -> u32 { 1 }
fn a() -> u32 {
    let c = || {
        use genmacros::Debug;
        #[derive(Debug)]
        struct T;
        one()
    };
    c()
}
#[test]
fn other_derive() { assert_eq!(a(), 2); }
