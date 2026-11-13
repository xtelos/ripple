// serde_derive >= 1.0.163 shape. Expect: compiles and prints 1 (nothing entered the scope).
mod util {
    #[allow(non_snake_case)]
    pub fn _IMPL_SERIALIZE_FOR_Foo() -> u8 {
        1
    }
}
use util::*;
#[derive(pm::UnderscoreConst)]
struct Foo;
fn main() {
    println!("glob fn ran: {}", _IMPL_SERIALIZE_FOR_Foo());
}
