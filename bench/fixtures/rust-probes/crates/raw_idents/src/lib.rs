#![allow(warnings)]
pub fn r#match() -> u32 { 1 }
pub fn r#fn() -> u32 { 2 }
mod r#mod {
    pub fn r#loop() -> u32 { 3 }
}
pub fn run() -> u32 { r#match() + r#fn() + r#mod::r#loop() + self::r#match() }
