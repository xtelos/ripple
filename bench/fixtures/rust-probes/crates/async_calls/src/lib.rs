#![allow(warnings)]
async fn f() -> u32 { 1 }
async fn g() -> u32 { f().await + self::f().await }
pub async fn run() -> u32 { g().await + f().await }
pub fn make() -> impl std::future::Future<Output = u32> { run() }
pub fn block() -> impl std::future::Future<Output = u32> { async { f().await + g().await } }
