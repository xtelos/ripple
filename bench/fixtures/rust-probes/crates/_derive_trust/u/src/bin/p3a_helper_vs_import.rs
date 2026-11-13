// A derive helper `command` and an imported attribute macro `command`, both in scope. Expect (if rustc rejects): E0659.
use pm::command;
#[derive(pm::D)]
#[command]
struct S;
fn main() {}
