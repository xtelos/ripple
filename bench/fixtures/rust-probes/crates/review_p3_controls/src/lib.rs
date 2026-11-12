extern crate p3dep as dep_alias;

pub fn f() -> u32 {
    1
}
pub fn g() -> u32 {
    2
}

pub mod a {
    pub fn f() -> u32 {
        10
    }
    pub fn g() -> u32 {
        20
    }
}
pub mod b {
    pub fn f() -> u32 {
        100
    }
}

pub mod explicit_beats_glob {
    use crate::a::*;
    use crate::b::f;
    pub fn run() -> u32 {
        f() + g()
    }
}

pub mod two_globs {
    use crate::a::*;
    use crate::b::*;
    pub fn run() -> u32 {
        g()
    }
}

pub fn params(f: fn() -> u32, g: impl Fn() -> u32) -> u32 {
    f() + g()
}

pub fn closure_local() -> u32 {
    let f = || 3;
    f()
}

pub struct B;
impl B {
    pub fn new() -> B {
        B
    }
}
pub type A = B;
pub fn alias_call() -> B {
    A::new()
}

pub trait T1 {
    fn same() -> u32;
    fn dflt() -> u32 {
        Self::same()
    }
}
pub trait T2 {
    fn same() -> u32;
}
pub struct Two;
impl T1 for Two {
    fn same() -> u32 {
        1
    }
}
impl T2 for Two {
    fn same() -> u32 {
        2
    }
}
pub struct Inh;
impl Inh {
    pub fn same() -> u32 {
        7
    }
}
impl T1 for Inh {
    fn same() -> u32 {
        8
    }
}
pub fn type_calls(x: &Two) -> u32 {
    let _ = x;
    <Two as T1>::same() + <Two as T2>::same() + Inh::same() + <Inh as T1>::same()
}
pub fn ambiguous() -> u32 {
    // Two has two traits supplying `same`: not written as Two::same (that would not compile)
    0
}

pub fn generic<X: T1>() -> u32 {
    X::same() + <X as T1>::same()
}

pub fn ext_alias() -> u32 {
    dep_alias::depf() + p3dep::depf()
}

pub mod user_vec {
    macro_rules! vec {
        ($e:expr) => {
            stringify!($e)
        };
    }
    pub fn run() -> &'static str {
        // a user vec! that never evaluates its argument
        vec!(super::f())
    }
}
