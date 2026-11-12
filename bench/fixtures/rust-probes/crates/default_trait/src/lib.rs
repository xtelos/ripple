#![allow(warnings)]
#[derive(Default, Debug, Clone)]
pub struct D { pub x: u32 }
pub struct M;
impl Default for M {
    fn default() -> M { M }
}
pub struct G<T>(pub T);
impl<T: Default> Default for G<T> {
    fn default() -> Self { G(T::default()) }
}
pub fn run() -> u32 {
    let a = D::default();
    let b: D = Default::default();
    let c = <D as Default>::default();
    let m = M::default();
    let m2: M = Default::default();
    let m3 = <M as Default>::default();
    let g: G<u8> = G::default();
    let g2: G<M> = Default::default();
    let n = u32::default();
    let s = String::default();
    let o: Option<u8> = Default::default();
    let q: M = std::default::Default::default();
    let _ = (a, b, c, m, m2, m3, g, g2, s, o, q);
    n
}
