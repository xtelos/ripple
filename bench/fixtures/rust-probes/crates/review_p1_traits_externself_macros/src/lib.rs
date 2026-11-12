extern crate self as p1;

use std::borrow::Borrow;
use std::fmt;
use std::str::FromStr;

pub struct Inner;
impl fmt::Display for Inner {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("inner")
    }
}

pub struct Outer(pub Inner);
impl fmt::Display for Outer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // delegates to <Inner as Display>::fmt, which is repo code
        fmt::Display::fmt(&self.0, f)
    }
}

pub struct Num(pub u32);
impl FromStr for Num {
    type Err = ();
    fn from_str(_s: &str) -> Result<Num, ()> {
        Ok(Num(1))
    }
}
pub fn parse_num() -> Num {
    // reaches <Num as FromStr>::from_str
    FromStr::from_str("1").unwrap()
}

pub struct Name(pub String);
impl Borrow<str> for Name {
    fn borrow(&self) -> &str {
        &self.0
    }
}
pub fn borrow_self(n: &Name) -> &Name {
    // the std blanket `impl<T> Borrow<T> for T`, NOT the repo impl
    Name::borrow(n)
}

pub struct Celsius(pub f64);
impl From<f64> for Celsius {
    fn from(v: f64) -> Self {
        Celsius(v)
    }
}
pub fn reflexive(c: Celsius) -> Celsius {
    // the std reflexive `impl<T> From<T> for T`, NOT the repo From<f64>
    <Celsius as From<Celsius>>::from(c)
}

pub fn helper() -> u32 {
    7
}
pub fn via_self_alias() -> u32 {
    // reaches crate::helper through `extern crate self as p1`
    p1::helper()
}

pub mod deep {
    pub fn via_self_alias_in_child() -> u32 {
        p1::helper()
    }
}

macro_rules! bind {
    ($n:ident) => {
        let $n = || 99u32;
    };
}
pub fn macro_local() -> u32 {
    bind!(helper);
    // the closure `helper` the macro bound, NOT crate::helper
    helper()
}

mod a {
    #[allow(dead_code)]
    fn drop<T>(_x: T) {
        panic!("never called from b");
    }
    pub fn other() {}
}
pub mod b {
    use crate::a::*;
    pub fn run() {
        other();
        // a::drop is private to a, so the glob does not import it: this is std::mem::drop
        drop(5u8);
    }
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::macro_local(), 99);
        super::b::run();
        assert_eq!(super::reflexive(super::Celsius(2.5)).0, 2.5);
        assert_eq!(super::via_self_alias(), 7);
        let n = super::Name("x".into());
        assert!(std::ptr::eq(super::borrow_self(&n), &n));
    }
}
