#![allow(dead_code, unused)]
// The type name `S` in `s: &S` comes through a glob, and a macro (a `macro_rules!` of the crate, whose body is read and brings no file in)
// makes an explicit item of that name, which beats the glob: a struct, a type alias, or a `use ... as S`. In a block it makes a struct that
// shadows. rustc calls the made one's method in each. Ripple cannot see what a macro makes, so a name a macro may have made is not
// resolved, and the receiver's type is not known: no edge, with the crate read whole.
pub mod other {
    pub struct S;
    pub struct Other;

    impl S {
        pub fn m(&self) -> &'static str {
            "other::S::m"
        }
    }

    impl Other {
        pub fn m(&self) -> &'static str {
            "Other::m"
        }
    }
}

pub mod no_macro {
    use crate::other::*;

    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=E
    }
}

pub mod makes_a_struct {
    use crate::other::*;

    macro_rules! make {
        () => {
            pub struct S;
            impl S {
                pub fn m(&self) -> &'static str {
                    "macro-made S::m"
                }
            }
        };
    }
    make!();

    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=U:cannot resolve
    }
}

pub mod makes_an_alias {
    use crate::other::*;

    macro_rules! alias {
        () => {
            pub type S = Other;
        };
    }
    alias!();

    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=U:cannot resolve
    }
}

pub mod makes_a_use {
    use crate::other::*;

    macro_rules! import {
        () => {
            use crate::other::Other as S;
        };
    }
    import!();

    pub fn f(s: &S) -> &'static str {
        s.m() //~ m=U:cannot resolve
    }
}

pub mod makes_a_struct_in_a_block {
    use crate::other::*;

    macro_rules! make {
        () => {
            struct S;
            impl S {
                fn m(&self) -> &'static str {
                    "block-made S::m"
                }
            }
        };
    }

    pub fn f() -> &'static str {
        make!();
        let s: &S = &S;
        s.m() //~ m=U:in a block around the call
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_call_runs_the_method_the_compiler_picks() {
        assert_eq!(no_macro::f(&other::S), "other::S::m");
        assert_eq!(makes_a_struct::f(&makes_a_struct::S), "macro-made S::m");
        assert_eq!(makes_an_alias::f(&other::Other), "Other::m");
        assert_eq!(makes_a_use::f(&other::Other), "Other::m");
        assert_eq!(makes_a_struct_in_a_block::f(), "block-made S::m");
    }
}
