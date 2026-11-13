
fn b() -> String {
    #[derive(Debug)]
    struct T;
    let s = format!("{:?}", T);
    {
        #[allow(unused_imports)]
        use genmacros::Debug;
    }
    s
}
#[test]
fn std_derive() { assert_eq!(b(), "T"); }
