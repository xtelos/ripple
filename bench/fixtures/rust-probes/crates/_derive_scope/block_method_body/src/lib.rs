
fn one() -> u32 { 1 }
pub struct W;
impl W {
    fn a(&self) -> u32 {
        use genmacros::Debug;
        #[derive(Debug)]
        struct T;
        one()
    }
}
#[test]
fn other_derive() { assert_eq!(W.a(), 2); }
