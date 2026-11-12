#![allow(warnings)]
#[path = "other/real.rs"]
mod renamed;
mod plain;
pub mod inl {
    #[path = "x.rs"]
    pub mod viaattr;
}
pub fn run() -> u32 { renamed::a() + plain::b() + inl::viaattr::c() + renamed::sub::d() }
