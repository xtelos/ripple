#![no_std]
extern crate std as real;
pub use real::*;
pub mod prelude {
    pub mod rust_2021 {
        pub use real::prelude::rust_2021::*;
    }
}
pub fn mark() -> u32 { 2 }
