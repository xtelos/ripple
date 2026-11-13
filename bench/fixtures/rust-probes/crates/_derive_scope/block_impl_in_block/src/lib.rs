
fn one() -> u32 { 1 }
fn a() -> u32 {
    use genmacros::Debug;
    struct W;
    impl W {
        fn m(&self) -> u32 {
            #[derive(Debug)]
            struct T;
            one()
        }
    }
    W.m()
}
#[test]
fn other_derive() { assert_eq!(a(), 2); }
