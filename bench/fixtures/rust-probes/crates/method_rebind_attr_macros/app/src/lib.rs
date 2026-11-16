#![allow(dead_code, unused)]
// An attribute macro on the impl, the function or the module that holds a call, and an item macro of the module, each rebinds what the
// call means: `Self`, a parameter's type, or the type name a glob gives. rustc calls the other method in each. A macro at the module level
// may also bring a file in, so ripple refuses the whole crate (Index.method_taint), and the checks that matter here are the per-scope
// rules under it: tests/test_rs_method_unread.py runs this crate with that refusal off and asks that no edge be wrong.
pub struct S;
pub struct Other;

impl S {
    pub fn m(&self) -> &'static str {
        "S::m"
    }
}

impl Other {
    pub fn m(&self) -> &'static str {
        "Other::m"
    }
}

pub mod retarget_impl {
    use super::*;

    #[pm::retarget]
    impl S {
        pub fn g(&self) -> &'static str {
            self.m()
        }
    }
}

pub mod swap_fn {
    use super::*;

    #[pm::swap_all]
    pub fn f(s: &S) -> &'static str {
        s.m()
    }
}

#[pm::swap_all]
pub mod swap_mod {
    use super::*;

    pub fn f(s: &S) -> &'static str {
        s.m()
    }
}

pub mod glob_and_item_macro {
    use crate::other::*;

    pm::make_s!();

    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=U:cannot resolve
    }
}

pub mod other {
    pub struct S;

    impl S {
        pub fn m(&self) -> &'static str {
            "other::S::m"
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_call_runs_the_method_the_compiler_picks() {
        assert_eq!(Other.g(), "Other::m");
        assert_eq!(swap_fn::f(&Other), "Other::m");
        assert_eq!(swap_mod::f(&Other), "Other::m");
        assert_eq!(glob_and_item_macro::f(&glob_and_item_macro::S), "macro-made S::m");
    }
}
