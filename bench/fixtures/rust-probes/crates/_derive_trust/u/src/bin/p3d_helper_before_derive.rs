// The imported attribute macro written BEFORE the derive that declares the helper. Expect: E0659 or the macro runs.
use pm::command;
#[command]
#[derive(pm::D)]
struct S;
fn main() {
    injected_by_command();
}
