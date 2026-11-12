#![allow(warnings)]
use std::fmt;
pub struct W(pub u32);
impl fmt::Display for W {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{}", self.0) }
}
pub struct V;
impl ToString for V {
    fn to_string(&self) -> String { String::new() }
}
pub fn run(w: &W, v: &V) -> usize {
    ToString::to_string(w).len()
        + W::to_string(w).len()
        + <W as ToString>::to_string(w).len()
        + V::to_string(v).len()
        + ToString::to_string(v).len()
        + <V as ToString>::to_string(v).len()
}
