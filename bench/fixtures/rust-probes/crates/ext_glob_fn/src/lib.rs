#![allow(warnings)]
fn helper() -> u32 { 1 }
fn repo_only() -> u32 { 2 }
pub fn block_glob() -> u32 {
    use exty::*;
    // the block's glob brings exty::helper, which shadows the module's helper
    helper() + repo_only()
}
pub fn module_item() -> u32 { helper() }
mod g {
    use exty::*;
    fn extra() -> u32 { 4 }
    pub fn run() -> u32 { extra() + helper() }
}
pub fn path_call() -> u32 { exty::helper() + exty::extra() }
