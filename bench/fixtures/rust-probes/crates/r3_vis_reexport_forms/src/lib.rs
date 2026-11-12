#![allow(warnings)]
use std::sync::atomic::{AtomicU32, Ordering};
static RAN: AtomicU32 = AtomicU32::new(0);
fn mark(n: u32) { RAN.fetch_add(n, Ordering::SeqCst); }
pub fn ran() -> u32 { RAN.load(Ordering::SeqCst) }
fn boom() -> ! { panic!("an invisible drop ran") }

pub mod m1 { mod inner { pub(super) fn drop<T>(_: T) { crate::boom() } } pub use self::inner::*; }
pub mod m2 { mod inner { fn drop<T>(_: T) { crate::boom() } pub fn keep() {} } pub use self::inner::*; }
pub mod m3 { mod inner { pub(in crate::m3) fn drop<T>(_: T) { crate::boom() } } pub use self::inner::*; }
pub mod m4 { mod inner { pub(crate) fn drop<T>(_: T) { crate::mark(1000) } } pub(crate) use self::inner::*; }
pub mod m5 { mod inner { pub fn drop<T>(_: T) { crate::boom() } } use self::inner::*; }
pub mod m6 {
    mod mid { mod inner { pub(super) fn drop<T>(_: T) { crate::boom() } } pub(super) use self::inner::*; }
    pub use self::mid::*;
}
pub mod m7 { pub mod far { pub(crate) fn drop<T>(_: T) { crate::mark(10) } } pub use self::far::*; }
pub mod m8 { pub use crate::m7::*; }

pub mod u1 { use crate::m1::*; pub fn run() { drop(5u8) } }
pub mod u2 { use crate::m2::*; pub fn run() { drop(5u8) } }
pub mod u3 { use crate::m3::*; pub fn run() { drop(5u8) } }
pub mod u4 { use crate::m4::*; pub fn run() { drop(5u8) } }
pub mod u5 { use crate::m5::*; pub fn run() { drop(5u8) } }
pub mod u6 { use crate::m6::*; pub fn run() { drop(5u8) } }
pub mod u7 { pub fn run() { use crate::m8::*; drop(5u8) } }

#[cfg(test)]
mod t {
    use super::*;
    #[test]
    fn which() {
        u1::run(); u2::run(); u3::run(); u5::run(); u6::run();
        assert_eq!(ran(), 0);
        u4::run();
        assert_eq!(ran(), 1000);
        u7::run();
        assert_eq!(ran(), 1010);
    }
}
