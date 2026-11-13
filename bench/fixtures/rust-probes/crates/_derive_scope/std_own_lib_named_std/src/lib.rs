//! A crate a probe names `std` (a dependency keyed `std`, `extern crate fakestd as std;`, or a library called `std`): it re-exports
//! the real standard library and, in the preludes, genmacros' derive `Debug`, so the bare `Debug` of a derive is genmacros' wherever
//! the crate's `std` is this one.
#![no_std]
extern crate std as real;
pub use real::*;
pub mod prelude {
    pub mod rust_2021 {
        pub use real::prelude::rust_2021::*;
        pub use genmacros::Debug;
    }
}
