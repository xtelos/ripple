#![allow(warnings)]
fn tick() -> u32 { 7 }
#[tokio::main]
async fn main() {
    std::process::exit(if tick() == 7 { 0 } else { 3 });
}
