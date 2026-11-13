// Control: no derive declares `command`, the imported attribute macro runs. Expect: compiles (the injected fn exists).
use pm::command;
#[derive(Debug)]
#[command]
struct S;
fn main() {
    injected_by_command();
}
