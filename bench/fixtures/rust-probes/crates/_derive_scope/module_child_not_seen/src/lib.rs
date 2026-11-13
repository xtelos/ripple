
#[derive(Debug)]
pub struct S;
mod c {
    #[allow(unused_imports)]
    use genmacros::Debug;
}
#[test]
fn std_derive() { assert_eq!(format!("{:?}", S), "S"); }
