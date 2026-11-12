#![allow(warnings)]
mod a {
    pub use super::b::*;
    pub fn fa() -> u32 { 1 }
}
mod b {
    pub use super::a::*;
    pub fn fb() -> u32 { 2 }
}
mod c {
    use crate::a::*;
    pub fn run() -> u32 { fa() + fb() }
}
mod d {
    use crate::b::*;
    pub fn run() -> u32 { fa() + fb() }
}
