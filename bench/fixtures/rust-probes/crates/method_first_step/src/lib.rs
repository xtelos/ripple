#![allow(dead_code, unused)]
// The first step of rustc's method probe: an inherent method whose receiver form is the receiver's own form is found
// before any trait method, and one that takes another form is found only at a later step (after the by-value trait methods).
// The marker comments on a call say what ripple must do (E: draw the edge the compiler's answer gives; U:text: leave it unresolved
// with a reason that has the text in it), as tests/test_rs_method_probes.py reads them.

pub struct S {
    pub n: u8,
}

pub trait Named {
    fn name(&self) -> u8;
    fn take(self) -> u8;
}

impl Named for S {
    fn name(&self) -> u8 {
        1
    }
    fn take(self) -> u8 {
        2
    }
}

impl S {
    pub fn name(&self) -> u8 {
        3
    }
    pub fn bump(&mut self) {
        self.n += 1;
    }
    pub fn take_value(self) -> u8 {
        self.n
    }
    // a `&self` method named like a trait method that takes `self`
    pub fn take(&self) -> u8 {
        4
    }
    pub fn from_ref(&self) -> u8 {
        self.take() //~ take=E
    }
    pub fn from_ref_name(&self) -> u8 {
        self.name() //~ name=E
    }
    pub fn from_mut(&mut self) -> u8 {
        self.bump(); //~ bump=E
        self.name() //~ name=U:later probe step
    }
    pub fn from_value(self) -> u8 {
        self.take_value() //~ take_value=E
    }
    pub fn from_value_bump(mut self) -> u8 {
        self.bump(); //~ bump=U:later probe step
        self.n
    }
}

pub fn by_ref(s: &S) -> u8 {
    s.name() //~ name=E
}

pub fn by_mut(s: &mut S) {
    s.bump(); //~ bump=E
}

pub fn by_mut_then_ref(s: &mut S) -> u8 {
    s.name() //~ name=U:later probe step
}

pub fn by_value(s: S) -> u8 {
    s.take_value() //~ take_value=E
}

// the trait's `take(self)` is found at the by-value step, before the inherent `take(&self)` at the autoref step
pub fn trait_wins(s: S) -> u8 {
    s.take() //~ take=U:later probe step
}

pub fn trait_wins_ref_name(s: S) -> u8 {
    s.name() //~ name=U:later probe step
}

pub fn typed_let() -> u8 {
    let s: S = S { n: 0 };
    s.take_value() //~ take_value=E
}

pub fn typed_let_ref(a: &S) -> u8 {
    let r: &S = a;
    r.name() //~ name=E
}

pub fn untyped_let() -> u8 {
    let s = S { n: 0 };
    s.take_value() //~ take_value=U:does not write
}

pub fn parenthesized(s: &S) -> u8 {
    (s).name() //~ name=E
}

pub fn chain(s: &S) -> String {
    s.name().to_string() //~ name=E; to_string=U:not a plain name
}

pub fn in_closure(s: &S) -> u8 {
    let f = || s.name(); //~ name=E
    f()
}

pub fn in_std_macro(s: &S) {
    println!("{}", s.name()); //~ name=E
    assert_eq!(s.name(), 3); //~ name=E
}
