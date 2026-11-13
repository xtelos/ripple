
fn a() -> String {
    #[allow(unused_imports)]
    use genmacros::Debug;
    mod m {
        #[derive(Debug)]
        pub struct T;
        pub fn s() -> String { format!("{:?}", T) }
    }
    m::s()
}
#[test]
fn std_derive() { assert_eq!(a(), "T"); }
