#![allow(warnings)]
mod tr {
    pub trait Hello {
        fn hello() -> u32;
        fn greet(&self) -> u32 { 5 }
    }
}
pub struct S;
impl tr::Hello for S {
    fn hello() -> u32 { 1 }
}
mod user {
    use crate::tr::Hello as _;
    pub fn run(s: &crate::S) -> u32 { crate::S::hello() + s.greet() }
}
mod user2 {
    use std::fmt::Write as _;
    pub fn run() -> String {
        let mut s = String::new();
        s.write_str("x").unwrap();
        s
    }
}
