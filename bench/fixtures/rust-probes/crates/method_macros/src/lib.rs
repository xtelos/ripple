#![allow(dead_code, unused)]
// A macro_rules! macro whose body is read and brings no file in may have made an impl, which cannot change which inherent method a call
// finds (rustc rejects a second method of the name for the type, E0592): the visible method is the edge, and a method only the macro made
// is not one ripple knows. In the module that invokes it, a name the macro may have made (a const called `s`) is the one thing the
// binding of a typed receiver could be: that stays a refusal, a rule of the scope and not of the crate.

pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub mod made_impls {
    use super::S;

    macro_rules! make {
        ($t:ident) => {
            impl $t {
                pub fn made(&self) -> u8 {
                    5
                }
            }
        };
    }

    make!(S);

    pub fn in_the_module_of_the_macro(s: &S) -> u8 {
        s.go() //~ go=U:binds nothing
    }
}

pub fn calls(s: &S) -> u8 {
    s.go() //~ go=E
}

pub fn calls_made(s: &S) -> u8 {
    s.made() //~ made=U:no inherent impl ripple read
}

pub fn in_std_macro(s: &S) {
    println!("{}", s.go()); //~ go=E
}
