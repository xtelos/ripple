#![allow(warnings)]
fn one() -> u32 { 1 }
fn two() -> u32 { 2 }

// `rustfmt`, `clippy` and `diagnostic` are tool namespaces, and a `mod`, a `use` or an `extern crate` of the name makes the path
// another macro (rustc builds each). fakemacros rewrites a call of `one` into a call of `two`, so each function below that it
// rewrites calls `two`, though it was written as a call of `one`: ripple must not draw an edge to `one`.
pub mod via_mod {
    mod rustfmt {
        pub use fakemacros::rewrite as skip;
    }
    #[rustfmt::skip]
    #[tokio::main]
    pub async fn a() -> u32 { super::one() }
}
pub mod via_use {
    use fakemacros as rustfmt;
    #[rustfmt::rewrite]
    #[tokio::main]
    pub async fn b() -> u32 { super::one() }
}
pub mod via_extern_crate {
    extern crate fakemacros as clippy;
    #[clippy::rewrite]
    pub fn c() -> u32 { super::one() }
}
pub mod via_bare_use {
    use fakemacros::rewrite as diagnostic;
    #[diagnostic]
    #[tokio::main]
    pub async fn d() -> u32 { super::one() }
}
pub mod via_block_mod {
    pub fn e() -> u32 {
        mod rustfmt {
            pub use fakemacros::rewrite as skip;
        }
        #[rustfmt::skip]
        fn inner() -> u32 { super::one() }
        inner()
    }
}
// any other name of the compiler's list works the same way as a path: `#[doc::foo]` builds only through a module called doc
pub mod via_doc_path {
    mod doc {
        pub use fakemacros::rewrite as foo;
    }
    #[doc::foo]
    pub fn f() -> u32 { super::one() }
}

#[cfg(test)]
mod t {
    use super::*;

    #[test]
    fn which() {
        assert_eq!(
            (via_mod::a(), via_use::b(), via_extern_crate::c(), via_bare_use::d(), via_block_mod::e(), via_doc_path::f()),
            (2, 2, 2, 2, 2, 2)
        );
    }
}
