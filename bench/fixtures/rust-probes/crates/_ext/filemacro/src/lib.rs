//! A macro that brings a file in: `helper!();` at the module level of the crate that uses it expands to `mod x;` read from `../shared/x.rs`
//! (relative to the file that invokes it). A probe uses it to show a `#[macro_use] extern crate` can give a name a macro that a repo
//! macro of the same name, in another module, is not.
#[macro_export]
macro_rules! helper {
    () => {
        #[path = "../shared/x.rs"]
        mod x;
    };
}
