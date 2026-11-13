// serde_derive <= 1.0.162 shape. Expect: E0618, the generated const (type ()) shadows the glob-imported fn, so the call fails.
mod util {
    #[allow(non_snake_case)]
    pub fn _IMPL_SERIALIZE_FOR_Foo() -> u8 {
        1
    }
}
use util::*;
#[derive(pm::NamedConst)]
struct Foo;
fn main() {
    let _ = _IMPL_SERIALIZE_FOR_Foo();
}
