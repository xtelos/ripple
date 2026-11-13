// thiserror-impl <= 1.0.48 shape `impl std::fmt::Display for S`: with a local `mod std`, the derive implements the REPO trait.
// Expect: compiles (S implements crate::std::fmt::Display).
mod std {
    pub mod fmt {
        pub trait Display {
            fn fmt(&self, f: &mut ::core::fmt::Formatter) -> ::core::fmt::Result;
        }
    }
}
#[derive(pm::RelStd)]
struct S;
fn need<T: crate::std::fmt::Display>() -> &'static str {
    "S implements the local std::fmt::Display"
}
fn main() {
    ::std::println!("{}", need::<S>());
}
