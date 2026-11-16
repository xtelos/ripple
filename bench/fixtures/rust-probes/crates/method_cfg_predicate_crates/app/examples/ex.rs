// An example is a crate of its own; `test = true` in the manifest builds it with `cfg(test)` for `cargo test`, linking the library built without it.
fn main() {}

#[cfg(test)]
mod ex {
    use app::{S, Tr};
    fn via(s: &S) -> &'static str {
        s.m() //~ m=U:cfg
    }
    fn make() -> &'static str {
        S::make() //@ make=U:cfg
    }
    #[test]
    fn the_example_finds_the_trait_s_method() {
        assert_eq!(via(&S), "Tr::m");
        assert_eq!(make(), "Tr::make");
    }
}
