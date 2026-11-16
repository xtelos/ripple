#![allow(dead_code, unused)]
// A statement `println!(s);` binds nothing, as long as `println!` is the standard macro; a name ripple trusts as one is not, here,
// each in its own way (a `macro_rules!` of that name in the module, in the function, behind a `#[macro_use]` module, or a `use ... as`).
// Each macro rebinds `s` to an `Other`, so rustc calls Other::m. The crate is read whole: only these names tell ripple not to trust it.
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

pub mod trusted {
    use super::*;

    pub fn f(s: &S) -> &'static str {
        format!("{}", 1);
        s.m() //~ m=E
    }
}

pub mod in_the_module {
    use super::*;

    macro_rules! println {
        ($i:ident) => {
            let $i = &Other;
        };
    }

    pub fn f(s: &S) -> &'static str {
        println!(s);
        s.m() //~ m=U:in a block around the call
    }
}

pub mod in_the_function {
    use super::*;

    pub fn f(s: &S) -> &'static str {
        macro_rules! assert {
            ($i:ident) => {
                let $i = &Other;
            };
        }
        assert!(s);
        s.m() //~ m=U:in a block around the call
    }
}

pub mod renamed_by_a_use {
    use super::*;

    macro_rules! bind {
        ($i:ident) => {
            let $i = &Other;
        };
    }
    pub(crate) use bind as dbg;

    pub fn f(s: &S) -> &'static str {
        dbg!(s);
        s.m() //~ m=U:in a block around the call
    }
}

#[macro_use]
mod macros {
    macro_rules! vec {
        ($i:ident) => {
            let $i = &crate::Other;
        };
    }
}

pub mod after_a_macro_use_module {
    use super::*;

    pub fn f(s: &S) -> &'static str {
        vec!(s);
        s.m() //~ m=U:in a block around the call
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_call_runs_the_method_the_compiler_picks() {
        assert_eq!(trusted::f(&S), "S::m");
        assert_eq!(in_the_module::f(&S), "Other::m");
        assert_eq!(in_the_function::f(&S), "Other::m");
        assert_eq!(renamed_by_a_use::f(&S), "Other::m");
        assert_eq!(after_a_macro_use_module::f(&S), "Other::m");
    }
}
