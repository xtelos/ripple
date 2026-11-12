#![allow(warnings)]
mod a {
    pub fn f() -> u32 { 1 }
    pub fn g() -> u32 { 2 }
    pub fn only_a() -> u32 { 3 }
}
mod b {
    pub fn f() -> u32 { 10 }
    pub fn g() -> u32 { 20 }
    pub fn only_b() -> u32 { 30 }
}
mod user {
    use crate::a::f;
    use crate::b::*;
    fn g() -> u32 { 200 }
    pub fn run() -> u32 { f() + g() + only_b() }
    pub fn run_block() -> u32 {
        use crate::b::f;
        f()
    }
}
mod user2 {
    use crate::a::*;
    use crate::b::only_b;
    pub fn run() -> u32 { only_a() + only_b() }
}
