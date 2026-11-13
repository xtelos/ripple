
#[allow(unused_imports)]
fn a() { use genmacros::Debug; }
fn b() -> String {
    #[derive(Debug)]
    struct T;
    format!("{:?}", T)
}
#[test]
fn std_derive() { assert_eq!(b(), "T"); }
