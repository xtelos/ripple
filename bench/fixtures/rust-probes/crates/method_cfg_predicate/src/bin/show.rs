// The build without `cfg(test)`: what the calls that exist in it call. `cargo run --bin show` prints the trait's default for each (the impls
// under `cfg(test)` are gone, and the ones under `foo = "a  b"` and `foo = "ab"` never existed); `cargo test` runs the impl of `r2c`.
fn main() {
    for (name, ran) in method_cfg_predicate::answers() {
        println!("{name}: {ran}");
    }
}
