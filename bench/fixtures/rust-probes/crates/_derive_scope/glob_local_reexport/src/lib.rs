
mod a { pub use genmacros::Debug; }
use a::*;
#[derive(Debug)]
pub struct S;
