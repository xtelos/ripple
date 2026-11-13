// #[serde(remote)] shape. Expect: prints "inherent from the derive": the generated inherent fn beats the repo trait impl.
trait T {
    fn serialize() -> &'static str;
}
#[derive(pm::Remote)]
struct Foo;
impl T for Foo {
    fn serialize() -> &'static str {
        "repo trait impl"
    }
}
fn main() {
    println!("{}", Foo::serialize());
}
