#![allow(dead_code, unused)]
// A type of one crate called from another: its inherent impls are all in its own crate (every module of it), and a method that is
// private to the crate is not one the other crate's call can reach.

mod more;

pub struct Cfg {
    pub n: u8,
}

pub trait Describe {
    fn get(&self) -> u8;
    fn internal(&self) -> u8;
}

impl Describe for Cfg {
    fn get(&self) -> u8 {
        10
    }
    fn internal(&self) -> u8 {
        11
    }
}

impl Cfg {
    pub fn get(&self) -> u8 {
        self.n
    }
    pub(crate) fn internal(&self) -> u8 {
        0
    }
    pub fn inside(&self) -> u8 {
        self.internal() //~ internal=E
    }
}
