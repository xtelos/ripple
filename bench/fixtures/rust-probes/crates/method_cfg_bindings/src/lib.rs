#![allow(dead_code, unused, unexpected_cfgs)]
// A `#[cfg]` or a `#[cfg_attr]` that may remove the receiver's own binding: the name is then the module's fn `s`, and the blanket
// `Tr` gives it a method `m`. A `cfg_attr` of a lint, which cannot remove anything, is not a reason.
pub struct S;

impl S {
    pub fn m(&self) -> &'static str {
        "S::m"
    }
}

pub trait Tr {
    fn m(&self) -> &'static str;
}

impl<F: Fn() -> u8> Tr for F {
    fn m(&self) -> &'static str {
        "Tr::m"
    }
}

fn s() -> u8 {
    0
}

pub fn let_off() -> &'static str {
    #[cfg(any())]
    let s: &S = &S;
    s.m() //~ m=U:cfg
}

pub fn param_off(#[cfg(any())] s: &S) -> &'static str {
    s.m() //~ m=U:cfg
}

pub fn let_feature() -> &'static str {
    #[cfg(feature = "x")]
    let s: &S = &S;
    s.m() //~ m=U:cfg
}

pub fn let_cfg_attr_makes_a_cfg() -> &'static str {
    #[cfg_attr(all(), cfg(any()))]
    let s: &S = &S;
    s.m() //~ m=U:cfg
}

pub fn param_cfg_attr_makes_a_cfg(#[cfg_attr(all(), cfg(any()))] s: &S) -> &'static str {
    s.m() //~ m=U:cfg
}

pub fn let_on() -> &'static str {
    #[cfg(all())]
    let s: &S = &S;
    s.m() //~ m=U:cfg
}

pub fn let_cfg_attr_of_a_lint() -> &'static str {
    #[cfg_attr(all(), allow(unused_variables))]
    let s: &S = &S;
    s.m() //~ m=E
}

pub fn param_cfg_attr_of_a_lint(#[cfg_attr(all(), allow(unused_variables))] s: &S) -> &'static str {
    s.m() //~ m=E
}

pub fn let_with_a_lint() -> &'static str {
    #[allow(unused_variables)]
    let s: &S = &S;
    s.m() //~ m=E
}

#[cfg(test)]
mod proof {
    use super::*;

    #[test]
    fn which_runs() {
        assert_eq!(let_off(), "Tr::m");
        assert_eq!(param_off(), "Tr::m");
        assert_eq!(let_feature(), "Tr::m");
        assert_eq!(let_cfg_attr_makes_a_cfg(), "Tr::m");
        assert_eq!(param_cfg_attr_makes_a_cfg(), "Tr::m");
        assert_eq!(let_on(), "S::m");
        assert_eq!(let_cfg_attr_of_a_lint(), "S::m");
        assert_eq!(param_cfg_attr_of_a_lint(&S), "S::m");
        assert_eq!(let_with_a_lint(), "S::m");
    }
}
