// The attribute macro is imported in another module only. Expect: compiles; `#[command]` is the helper, so nothing is injected
// (the call below must fail with E0425 if the helper is used).
mod other {
    #[allow(unused_imports)]
    use pm::command;
}
#[derive(pm::D)]
#[command]
struct S;
fn main() {
    injected_by_command();
}
