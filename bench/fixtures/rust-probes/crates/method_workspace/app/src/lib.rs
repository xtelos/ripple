#![allow(dead_code, unused)]

use kit::{Cfg, Describe};

pub fn from_the_other_crate(c: &Cfg) -> u8 {
    c.get() //~ get=E
}

pub fn spread_over_files(c: &Cfg) -> u8 {
    c.more() //~ more=E
}

// `internal` is `pub(crate)` in kit: here the trait's is the one found
pub fn crate_private(c: &Cfg) -> u8 {
    c.internal() //~ internal=U:not visible
}
