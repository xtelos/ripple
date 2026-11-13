// Expect: compiles. The name, as a value, is the generated const `()`, not the glob's fn: the const entered the scope.
mod util {
    #[allow(non_snake_case)]
    pub fn _IMPL_SERIALIZE_FOR_Foo() -> u8 {
        1
    }
}
#[allow(unused_imports)]
use util::*;
#[derive(pm::NamedConst)]
struct Foo;
fn main() {
    let x: () = _IMPL_SERIALIZE_FOR_Foo;
    println!("value is the generated const: {:?}", x);
}
