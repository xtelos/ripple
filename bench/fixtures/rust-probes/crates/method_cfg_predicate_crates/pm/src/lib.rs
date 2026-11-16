#![allow(dead_code, unused, unexpected_cfgs)]
// A procedural macro crate is a crate of its own (built for the host, with its own `cfg(test)`), linking the library built without it.
#[cfg(test)]
mod t {
    use app::{S, Tr};
    fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        S::make() //@ make=U:cfg
    }
    #[test]
    fn the_macro_crate_finds_the_trait_s_method() {
        assert_eq!(via(&S), "Tr::m");
        assert_eq!(make(), "Tr::make");
    }
}
