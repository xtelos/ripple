#![allow(dead_code, unused)]
// How the receiver's type is named: `Self`, an alias, a renamed import, a generic parameter or a local type that takes the name.

pub struct S;

pub trait Tr {
    fn go(&self) -> u8;
}

impl S {
    pub fn go(&self) -> u8 {
        1
    }
    pub fn make() -> S {
        S
    }
    pub fn with_ref(&self, other: &Self) -> u8 {
        other.go() //~ go=E
    }
    pub fn with_value(&self, other: Self) -> u8 {
        other.go() //~ go=U:later probe step
    }
    pub fn let_self(&self) -> u8 {
        let s: &Self = self;
        s.go() //~ go=E
    }
    pub fn let_self_mut(&mut self) -> u8 {
        let s: &mut Self = self;
        s.go() //~ go=U:later probe step
    }
}

pub type Alias = S;

pub fn via_alias(a: &Alias) -> u8 {
    a.go() //~ go=E
}

pub mod inner {
    pub use super::S as Renamed;
}

pub fn via_rename(r: &inner::Renamed) -> u8 {
    r.go() //~ go=E
}

pub fn via_path(r: &crate::S) -> u8 {
    r.go() //~ go=E
}

// `S` is the generic parameter here, whose bound `Tr` gives the method, not the struct `S`
pub fn generic_shadows<S: Tr>(s: &S) -> u8 {
    s.go() //~ go=U:generic
}

pub struct G<T>(pub T);

impl<T> G<T> {
    pub fn id(&self) -> u8 {
        0
    }
}

pub type GU = G<u8>;

pub fn through_generic_alias(g: &GU) -> u8 {
    g.id() //~ id=E
}

pub struct H;

pub type AliasOfH = H;

impl AliasOfH {
    pub fn hi(&self) -> u8 {
        1
    }
}

// the impl is written through an alias: its type arguments are not read through it
pub fn impl_through_alias(h: &H) -> u8 {
    h.hi() //~ hi=U:type arguments
}

pub fn dyn_receiver(t: &dyn Tr) -> u8 {
    t.go() //~ go=U:trait object
}

pub fn impl_trait_receiver(t: &impl Tr) -> u8 {
    t.go() //~ go=U:trait object
}

pub fn boxed_receiver(b: Box<S>) -> u8 {
    b.go() //~ go=U:outside the repo
}

pub fn tuple_receiver(t: (S, S)) -> u8 {
    t.0.go() //~ go=U:not a plain name
}

pub fn reference_to_reference(r: &&S) -> u8 {
    r.go() //~ go=U:reference to a reference
}
