#![allow(dead_code, unused)]
// Inherent methods that may not exist: under `#[cfg]` (on the method, its impl, its module, the function it sits in) or `cfg_attr`.
// The feature `fast` is off, so the trait's method is the one found where an inherent one is cfg'd, and the compiler says so.

pub struct S;

pub trait Tr {
    fn m(&self) -> u8;
    fn n(&self) -> u8;
    fn o(&self) -> u8;
    fn p(&self) -> u8;
    fn q(&self) -> u8;
    fn r(&self) -> u8;
}

impl Tr for S {
    fn m(&self) -> u8 {
        1
    }
    fn n(&self) -> u8 {
        1
    }
    fn o(&self) -> u8 {
        1
    }
    fn p(&self) -> u8 {
        1
    }
    fn q(&self) -> u8 {
        1
    }
    fn r(&self) -> u8 {
        1
    }
}

impl S {
    #[cfg(feature = "fast")]
    pub fn m(&self) -> u8 {
        2
    }
    pub fn plain(&self) -> u8 {
        0
    }
    #[cfg_attr(feature = "fast", inline)]
    pub fn attr_only(&self) -> u8 {
        0
    }
    #[cfg_attr(feature = "fast", cfg(any()))]
    pub fn attr_cfg(&self) -> u8 {
        0
    }
}

#[cfg(feature = "fast")]
impl S {
    pub fn n(&self) -> u8 {
        2
    }
}

#[cfg(feature = "fast")]
mod fast {
    impl super::S {
        pub fn o(&self) -> u8 {
            2
        }
    }
}

mod slow {
    #![cfg(feature = "fast")]
    impl super::S {
        pub fn p(&self) -> u8 {
            2
        }
    }
}

#[cfg(feature = "fast")]
fn make_q() {
    impl S {
        pub fn q(&self) -> u8 {
            2
        }
    }
}

pub fn calls(s: &S) -> u8 {
    s.m() //~ m=U:cfg
}

pub fn calls_impl(s: &S) -> u8 {
    s.n() //~ n=U:cfg
}

pub fn calls_module(s: &S) -> u8 {
    s.o() //~ o=U:cfg
}

pub fn calls_inner_attribute_module(s: &S) -> u8 {
    s.p() //~ p=U:cfg
}

pub fn calls_fn_body_impl(s: &S) -> u8 {
    s.q() //~ q=U:cfg
}

pub fn plain(s: &S) -> u8 {
    s.plain() //~ plain=E
}

pub fn cfg_attr_of_a_hint_removes_nothing(s: &S) -> u8 {
    s.attr_only() //~ attr_only=E
}

pub fn cfg_attr_may_hold_a_cfg(s: &S) -> u8 {
    s.attr_cfg() //~ attr_cfg=U:cfg
}

pub fn calls_trait_only(s: &S) -> u8 {
    s.r() //~ r=U:no inherent
}
