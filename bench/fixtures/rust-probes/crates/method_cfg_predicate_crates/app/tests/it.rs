// An integration test is a crate of its own, built with `cfg(test)`, linking the library built without it.
#[cfg(test)]
mod it {
    use app::{S, Tr};
    fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        S::make() //@ make=U:cfg
    }
    #[test]
    fn the_integration_test_finds_the_trait_s_method() {
        assert_eq!(via(&S), "Tr::m");
        assert_eq!(make(), "Tr::make");
    }
}
