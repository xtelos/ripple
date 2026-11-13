
fn b() -> String {
    {
        #[allow(unused_imports)]
        use genmacros::Debug;
    }
    {
        #[derive(Debug)]
        struct T;
        format!("{:?}", T)
    }
}
#[test]
fn std_derive() { assert_eq!(b(), "T"); }
