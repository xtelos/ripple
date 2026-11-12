#![allow(warnings)]
mod a {
    pub mod b {
        pub fn fb() -> u32 { 1 }
    }
    pub mod c {
        pub fn fc() -> u32 { 2 }
        pub fn fz() -> u32 { 3 }
    }
    pub use self::c::*;
}
mod user {
    use crate::a::{b::*, c::*};
    pub fn run() -> u32 { fb() + fc() + fz() }
}
mod user2 {
    use crate::a::*;
    pub fn run() -> u32 { fc() + b::fb() + c::fz() }
}
mod user3 {
    pub use crate::a::b::*;
    pub use crate::a::c::*;
}
pub fn run() -> u32 { user3::fb() + user3::fc() }
