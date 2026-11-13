// `use serde::Serialize` where serde has no `derive` feature (fakeserde: a trait only) and a glob brings another derive of the
// name. Expect: compiles and prints "fake derive": the explicit import gives the type namespace only, the macro comes from the glob.
use fakeserde::Serialize;
use reexp::*;
#[derive(Serialize)]
struct S;
impl Serialize for S {}
fn main() {
    println!("{}", S::marker());
}
