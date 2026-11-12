pub mod sub;
pub fn a() -> u32 { super::plain::b() + sub::d() }
