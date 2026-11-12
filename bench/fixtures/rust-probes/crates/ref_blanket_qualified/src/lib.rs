#![allow(warnings)]
pub trait Tr {
    fn f(&self) -> u32;
}
pub struct S;
impl Tr for S {
    fn f(&self) -> u32 { 1 }
}
impl<T: Tr> Tr for &T {
    fn f(&self) -> u32 { 10 + (**self).f() }
}
pub fn by_value(s: &S) -> u32 { <S as Tr>::f(s) }
pub fn by_ref(s: &S) -> u32 { <&S as Tr>::f(&s) }
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        let s = super::S;
        assert_eq!(super::by_value(&s), 1);
        // the blanket impl for &T runs, not the impl for S
        assert_eq!(super::by_ref(&s), 11);
    }
}
