#![allow(dead_code, unused, non_snake_case, non_camel_case_types)]
// A name a block around the call may give: an import (of a fn, a const, a unit struct, an alias), a glob, an item. Each is an
// item of the block, which stands inside the parameters and outside a `let` of the same block, and rustc tries it before them: the
// name the receiver is written as is not the binding the call site seems to have. Where a refusal is not what the compiler does
// (a `let` after the import wins), the marker still says U: ripple cannot tell the two apart.
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

pub mod other {
    pub fn s() -> u8 {
        0
    }
    pub fn t() -> u8 {
        1
    }
}

pub struct C;

impl C {
    pub fn m(&self) -> &'static str {
        "C::m"
    }
}

pub mod consts {
    pub const s: super::C = super::C;
}

pub mod units {
    pub struct s;
    impl s {
        pub fn m(&self) -> &'static str {
            "units::s::m"
        }
    }
}

pub fn use_fn(s: &S) -> &'static str {
    use crate::other::s;
    s.m() //~ m=U:in a block around the call
}

pub fn use_glob(s: &S) -> &'static str {
    use crate::other::*;
    s.m() //~ m=U:in a block around the call
}

pub fn use_in_outer_block(s: &S) -> &'static str {
    {
        use crate::other::*;
        {
            s.m() //~ m=U:in a block around the call
        }
    }
}

pub fn use_let_then_block() -> &'static str {
    let s: &S = &S;
    {
        use crate::other::s;
        s.m() //~ m=U:in a block around the call
    }
}

pub fn use_alias(x: &S) -> &'static str {
    use crate::other::t as x;
    x.m() //~ m=U:in a block around the call
}

pub fn use_list(s: &S) -> &'static str {
    use crate::other::{s, t};
    s.m() //~ m=U:in a block around the call
}

pub fn use_const(s: &S) -> &'static str {
    use crate::consts::s;
    s.m() //~ m=U:in a block around the call
}

pub fn use_unit_struct(s: &S) -> &'static str {
    use crate::units::s;
    s.m() //~ m=U:in a block around the call
}

pub fn use_after_the_call(s: &S) -> &'static str {
    let r = s.m(); //~ m=U:in a block around the call
    use crate::other::s;
    r
}

pub fn item_fn(s: &S) -> &'static str {
    fn s() -> u8 {
        0
    }
    s.m() //~ m=U:in a block around the call
}

pub fn let_after_the_use() -> &'static str {
    use crate::other::s;
    let s: &S = &S;
    s.m() //~ m=U:in a block around the call
}

pub fn use_of_another_name(s: &S) -> &'static str {
    use crate::other::t;
    s.m() //~ m=E
}

pub fn use_of_a_trait(s: &S) -> &'static str {
    use std::fmt::Debug;
    s.m() //~ m=E
}

pub fn use_in_a_sibling_block(s: &S) -> &'static str {
    {
        use crate::other::s;
        let _ = s();
    }
    s.m() //~ m=E
}

pub fn item_of_another_name(s: &S) -> &'static str {
    fn t() -> u8 {
        0
    }
    s.m() //~ m=E
}

// Items of the block that cannot give a value name (a type, a trait, an alias of a crate, a macro): the receiver is still the parameter.
// (A `mod s {}` in a block is one too, and so a file ripple does not read: tests/test_rs_method_names.py has it, a probe crate cannot hold it.)
pub fn type_alias_item(s: &S) -> &'static str {
    type s = u8;
    s.m() //~ m=E
}

pub fn enum_item(s: &S) -> &'static str {
    enum s {}
    s.m() //~ m=E
}

pub fn union_item(s: &S) -> &'static str {
    union s {
        a: u8,
    }
    s.m() //~ m=E
}

pub fn trait_item(s: &S) -> &'static str {
    trait s {}
    s.m() //~ m=E
}

pub fn extern_crate_item(s: &S) -> &'static str {
    extern crate std as s;
    s.m() //~ m=E
}

pub fn macro_definition_item(s: &S) -> &'static str {
    macro_rules! s {
        () => {};
    }
    s.m() //~ m=E
}

pub fn impl_item(s: &S) -> &'static str {
    impl S {
        pub fn other(&self) {}
    }
    s.m() //~ m=E
}

pub fn tuple_struct_item(s: &S) -> &'static str {
    struct s;
    impl s {
        pub fn m(&self) -> &'static str {
            "tuple::m"
        }
    }
    s.m() //~ m=U:in a block around the call
}

pub fn static_item(s: &S) -> &'static str {
    static s: C = C;
    s.m() //~ m=U:in a block around the call
}

pub fn const_item(s: &S) -> &'static str {
    const s: C = C;
    s.m() //~ m=U:in a block around the call
}

#[cfg(test)]
mod proof {
    use super::*;

    #[test]
    fn which_runs() {
        assert_eq!(use_fn(&S), "Tr::m");
        assert_eq!(use_glob(&S), "Tr::m");
        assert_eq!(use_in_outer_block(&S), "Tr::m");
        assert_eq!(use_let_then_block(), "Tr::m");
        assert_eq!(use_alias(&S), "Tr::m");
        assert_eq!(use_list(&S), "Tr::m");
        assert_eq!(use_const(&S), "C::m");
        assert_eq!(use_unit_struct(&S), "units::s::m");
        assert_eq!(use_after_the_call(&S), "Tr::m");
        assert_eq!(item_fn(&S), "Tr::m");
        assert_eq!(let_after_the_use(), "S::m");
        assert_eq!(use_of_another_name(&S), "S::m");
        assert_eq!(use_of_a_trait(&S), "S::m");
        assert_eq!(use_in_a_sibling_block(&S), "S::m");
        assert_eq!(item_of_another_name(&S), "S::m");
        assert_eq!(type_alias_item(&S), "S::m");
        assert_eq!(enum_item(&S), "S::m");
        assert_eq!(union_item(&S), "S::m");
        assert_eq!(trait_item(&S), "S::m");
        assert_eq!(extern_crate_item(&S), "S::m");
        assert_eq!(macro_definition_item(&S), "S::m");
        assert_eq!(impl_item(&S), "S::m");
        assert_eq!(const_item(&S), "C::m");
        assert_eq!(static_item(&S), "C::m");
        assert_eq!(tuple_struct_item(&S), "tuple::m");
    }
}
