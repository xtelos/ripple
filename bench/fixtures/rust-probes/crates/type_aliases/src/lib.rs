#![allow(warnings)]
pub struct B;
impl B {
    pub fn new() -> B { B }
    pub fn make(x: u32) -> u32 { x }
}
pub type A = B;
pub type AA = A;
pub type V = Vec<u8>;
pub type R<T> = Result<T, String>;
pub struct G<T>(pub T);
impl<T> G<T> {
    pub fn new(t: T) -> G<T> { G(t) }
}
pub type GU = G<u8>;
pub fn run() -> u32 {
    let _ = A::new();
    let _ = AA::new();
    let _ = V::new();
    let _ = V::with_capacity(4);
    let _ = GU::new(1);
    let _: R<u32> = Ok(1);
    AA::make(1) + <A>::make(2)
}
