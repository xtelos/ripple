#![allow(dead_code, unused, unexpected_cfgs)]
// Another member of the workspace: its `cfg(test)` is its own test build's, and its feature `f` its own (on by default here), and the library
// it links is built without `cfg(test)` and with its own `f` (off unless `--all-features`).
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
    fn the_member_finds_the_trait_s_method_under_cfg_test() {
        assert_eq!(via(&S), "Tr::m");
        assert_eq!(make(), "Tr::make");
    }
}

#[cfg(feature = "f")]
mod f {
    use app::{F, Tr};
    fn via(s: &F) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        F::make() //@ make=U:cfg
    }
    #[cfg(test)]
    #[test]
    fn the_member_finds_the_trait_s_method_unless_the_library_has_its_own_f() {
        assert_eq!(via(&F), if app::F_ON { "F::m" } else { "Tr::m" });
        assert_eq!(make(), if app::F_ON { "F::make" } else { "Tr::make" });
    }
}
