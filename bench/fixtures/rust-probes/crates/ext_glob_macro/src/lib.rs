#![allow(warnings)]
mod user {
    use exty::format;
    fn repo_fn() -> u32 { 5 }
    // exty's format! (imported by name) never evaluates its arguments
    pub fn run() -> usize { format!("{}", repo_fn()) }
}
mod plain {
    fn repo_fn() -> u32 { 5 }
    pub fn run() -> String { format!("{}", repo_fn()) }
}
mod explicit {
    use exty::vec;
    fn repo_fn() -> u32 { 5 }
    pub fn run() -> usize { vec![repo_fn()] }
}
