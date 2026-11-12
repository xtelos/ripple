#![allow(warnings)]
use std::fmt;
use std::fmt::Display;
pub struct Inner;
impl fmt::Display for Inner {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str("i") }
}
impl fmt::Debug for Inner {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str("I") }
}
pub struct Outer(pub Inner, pub u32);
impl fmt::Display for Outer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(&self.0, f)?;
        fmt::Debug::fmt(&self.0, f)?;
        <Inner as fmt::Display>::fmt(&self.0, f)?;
        Display::fmt(&self.0, f)?;
        std::fmt::Display::fmt(&self.0, f)?;
        fmt::Display::fmt(&self.1, f)?;
        <u32 as Display>::fmt(&self.1, f)?;
        Inner::fmt(&self.0, f)?;
        Ok(())
    }
}
