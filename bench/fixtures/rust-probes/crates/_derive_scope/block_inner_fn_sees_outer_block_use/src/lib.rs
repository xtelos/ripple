
fn one() -> u32 { 1 }
fn a() -> u32 {
    use genmacros::Debug;
    fn inner() -> u32 {
        #[derive(Debug)]
        struct T;
        one()
    }
    inner()
}
#[test]
fn other_derive() { assert_eq!(a(), 2); }
