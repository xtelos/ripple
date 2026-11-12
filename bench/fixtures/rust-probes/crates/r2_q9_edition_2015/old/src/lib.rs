#![allow(warnings)]
mod inner {
    pub fn f() -> u32 { 1 }
    pub fn g() -> u32 { 10 }
}
pub mod api {
    mod inner {
        pub fn f() -> u32 { 2 }
        pub fn g() -> u32 { 20 }
    }
    pub use inner::f;
    pub use inner::*;
    pub fn local() -> u32 { 3 }
}
pub use api::local as reexported;
pub fn calls_inner() -> u32 { inner::f() }
