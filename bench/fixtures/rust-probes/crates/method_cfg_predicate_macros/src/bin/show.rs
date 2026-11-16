// The build without `cfg(test)`: m1's calls (their `cfg`s deleted by the attribute macro) find the trait's method, the impl being gone; m2's find the
// impl, which the macro made unconditional. `cargo test` runs the impl for both.
fn main() {
    for (name, ran) in method_cfg_predicate_macros::answers() {
        println!("{name}: {ran}");
    }
}
