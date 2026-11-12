#![allow(warnings)]
#[derive(Default, Clone, Debug, PartialEq)]
pub struct D {
    pub x: u32,
}
pub trait Maker {
    fn default() -> u32;
    fn clone(&self) -> u32;
}
pub struct Other;
impl Maker for Other {
    fn default() -> u32 { 5 }
    fn clone(&self) -> u32 { 6 }
}
pub fn run() -> u32 {
    let a = D::default();
    let b = D::clone(&a);
    let c = <D as Default>::default();
    a.x + b.x + c.x + Other::default() + Other::clone(&Other)
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        // D's functions are the derives' (0), not Maker's (5 and 6)
        assert_eq!(super::run(), 11);
        assert_eq!(super::D::default().x, 0);
        assert_eq!(super::D::clone(&super::D { x: 3 }).x, 3);
    }
}
