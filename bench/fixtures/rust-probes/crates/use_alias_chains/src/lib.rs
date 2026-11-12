#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
    pub fn z() -> u32 { 9 }
}
mod b {
    pub use crate::a::f;
    pub use crate::a::z as zed;
}
mod c {
    pub use crate::b::f as g;
    pub use crate::b::zed as zz;
    pub(crate) use super::b::f as ff;
}
mod d {
    pub use crate::c::*;
}
pub fn run() -> u32 {
    b::f() + c::g() + c::zz() + d::g() + d::zz() + c::ff() + b::zed() + d::ff()
}
