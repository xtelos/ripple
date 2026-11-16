// The build without `cfg(test)`: what the calls that are not under their declaration's attribute call. `cargo run --bin show` prints the
// trait's default for every one of them (the impls are gone), where `cargo test` (the test `every_call_found_the_impl_with_test_on`) runs the impls.
fn main() {
    for (name, ran) in method_cfg_shared::answers() {
        println!("{name}: {ran}");
    }
}
