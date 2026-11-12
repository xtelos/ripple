#![allow(warnings)]
pub trait Tr {
    fn f(&self) -> u32;
    fn mk() -> u32 where Self: Sized { 1 }
}
pub struct S;
impl Tr for S {
    fn f(&self) -> u32 { 1 }
}
impl Tr for &S {
    fn f(&self) -> u32 { 2 }
}
impl Tr for Box<dyn Tr> {
    fn f(&self) -> u32 { 3 }
}
pub fn run(s: &S, b: Box<dyn Tr>, d: &dyn Tr) -> u32 {
    Tr::f(s)
        + <S as Tr>::f(s)
        + <&S as Tr>::f(&s)
        + Tr::f(&*b)
        + <dyn Tr as Tr>::f(d)
        + <Box<dyn Tr> as Tr>::f(&b)
        + S::mk()
        + <&S as Tr>::mk()
}
