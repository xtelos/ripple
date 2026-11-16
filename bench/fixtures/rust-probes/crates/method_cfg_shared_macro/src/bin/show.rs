// The build without `cfg(test)`: the macro's impl is there and the module's is not, so the plain call finds the macro's `make`.
fn main() {
    println!("without test: {}", method_cfg_shared_macro::without_test());
}
