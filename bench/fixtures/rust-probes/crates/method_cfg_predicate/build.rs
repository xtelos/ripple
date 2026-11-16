// A cfg with a value that has white space in it, set the way a build script sets one: `foo = "a b"` is on, and so is nothing else of that name.
// (Comparing predicates with their white space squashed would take `foo = "a  b"` and `foo = "ab"` for it; they are cfgs that are off.)
fn main() {
    println!("cargo:rustc-check-cfg=cfg(foo, values(any()))");
    println!("cargo:rustc-cfg=foo=\"a b\"");
}
