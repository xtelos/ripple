#![allow(warnings)]
// The binary reads x.rs as its own module `x`. The impl of x.rs is for the binary's own `S` (below), while the calls of x.rs are on the
// library's `twin::S`, and the library this binary links is built without `cfg(test)`: so the calls find the trait's methods.
mod x;

pub struct S;

fn main() {
    // the build without `cfg(test)`: `x::inner` does not exist in either crate, so there is nothing to call
    println!("x::inner is under cfg(test): nothing to call");
}

#[cfg(test)]
mod proof {
    #[test]
    fn the_binary_with_cfg_test_runs_the_trait_method_of_the_library() {
        assert_eq!(crate::x::inner::call(&twin::S), "Tr::m");
        assert_eq!(crate::x::inner::call_path(), "Tr::make");
    }
}
