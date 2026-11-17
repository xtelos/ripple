#![allow(dead_code, unused, unused_parens, unexpected_cfgs, non_local_definitions)]
// `Self::f()` and `<Self>::f()` under `#[cfg]`: which `f` runs depends on the features, and the call is an edge only where the function it
// names is built together with the call. Every `#[test]` below asserts which function ran, and passes under each of
//     cargo test --no-default-features      (a off, b off)
//     cargo test --no-default-features --features a
//     cargo test --no-default-features --features b
//     cargo test                            (a and b on: the build the oracle reads)
// (tests/test_rs_self_path_cfg.py states the rule). Markers, at the end of the line of the call, as in tests/test_rs_shared_cfg.py: the
// at-sign marker `f=E` is an edge to the `f` the compiler found, and `f=U:text` is unresolved with a reason that contains the text.
//
// Groups. A: the call is in an impl under no cfg or another one, and an impl for another spelling of the type (`W<A>` for `W<u8>`: a type
// alias, which ripple does not read as the same type) is the other `f` that may be built; the answer depends on the feature. B: the same
// shapes where the call and the function are under one predicate, which stay edges. C: a plain type, where the other `f` is a trait's
// default, or a second conditional impl. D: the other `f` is a trait's that no impl written for the type shows (a blanket impl, a provided
// method of `Iterator` or `PartialEq`, the reflexive `From` and `TryFrom`), or an impl for the type written another way (`W<(u8)>`,
// `[u8; 4usize]`). E: nothing is conditional and the inherent function wins over such a trait impl, so the call is an edge to it.

type A = u8;

pub trait Tr {
    fn f() -> &'static str {
        "Tr"
    }
}
pub trait Go {
    fn go() -> &'static str;
}
pub trait TrK {
    const K: &'static str = "TrK";
}

// ---- group A: the other `f` is for the same type written another way

// the call is in an impl under no cfg; one `f` per feature setting, in two conditional impls
pub struct W1<T>(pub T);
#[cfg(feature = "a")]
impl W1<A> {
    pub fn f() -> &'static str {
        "A1:alias"
    }
}
#[cfg(not(feature = "a"))]
impl W1<u8> {
    pub fn f() -> &'static str {
        "A1:u8"
    }
}
impl W1<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// `<Self>::f()` for the same
pub struct W2<T>(pub T);
#[cfg(feature = "a")]
impl W2<A> {
    pub fn f() -> &'static str {
        "A2:alias"
    }
}
#[cfg(not(feature = "a"))]
impl W2<u8> {
    pub fn f() -> &'static str {
        "A2:u8"
    }
}
impl W2<u8> {
    pub fn go() -> &'static str {
        <Self>::f() //@ f=U:several
    }
}

// the call is in a trait impl
pub struct W3<T>(pub T);
#[cfg(feature = "a")]
impl W3<A> {
    pub fn f() -> &'static str {
        "A3:alias"
    }
}
#[cfg(not(feature = "a"))]
impl W3<u8> {
    pub fn f() -> &'static str {
        "A3:u8"
    }
}
impl Go for W3<u8> {
    fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the cfg is on the function item, not on the impl
pub struct W4<T>(pub T);
impl W4<u8> {
    #[cfg(feature = "a")]
    pub fn f() -> &'static str {
        "A4:u8"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}
impl W4<A> {
    #[cfg(not(feature = "a"))]
    pub fn f() -> &'static str {
        "A4:alias"
    }
}

// the cfg is on an enclosing module
pub struct W5<T>(pub T);
#[cfg(feature = "a")]
mod m5 {
    use super::{A, W5};
    impl W5<A> {
        pub fn f() -> &'static str {
            "A5:alias"
        }
    }
}
#[cfg(not(feature = "a"))]
mod n5 {
    impl super::W5<u8> {
        pub fn f() -> &'static str {
            "A5:u8"
        }
    }
}
impl W5<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the cfg is a cfg_attr on the impl header
pub struct W6<T>(pub T);
#[cfg_attr(all(), cfg(feature = "a"))]
impl W6<A> {
    pub fn f() -> &'static str {
        "A6:alias"
    }
}
#[cfg_attr(all(), cfg(not(feature = "a")))]
impl W6<u8> {
    pub fn f() -> &'static str {
        "A6:u8"
    }
}
impl W6<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// `<Self>::f()` writes no type arguments, so the impl for `u8` is not known to apply; the other `f` is in an impl for every `T`
pub struct W17<T>(pub T);
#[cfg(not(feature = "a"))]
impl W17<u8> {
    pub fn f() -> &'static str {
        "A7:u8"
    }
}
#[cfg(feature = "a")]
impl<T> W17<T> {
    pub fn f() -> &'static str {
        "A7:generic"
    }
}
impl W17<u8> {
    pub fn go() -> &'static str {
        <Self>::f() //@ f=U:cfg
    }
}

// the other `f` is for the type written with parentheses, `W<(u8)>`, which is `W<u8>` though ripple compares the two as written
pub struct W19<T>(pub T);
#[cfg(feature = "a")]
impl W19<u8> {
    pub fn f() -> &'static str {
        "A9:u8"
    }
}
#[cfg(not(feature = "a"))]
impl W19<(u8)> {
    pub fn f() -> &'static str {
        "A9:paren"
    }
}
impl W19<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// ---- group B: the call and the function are under one predicate

// the impl of the call carries the predicate of the impl of the function (two impl blocks)
pub struct W7<T>(pub T);
#[cfg(feature = "a")]
impl W7<u8> {
    pub fn f() -> &'static str {
        "B1:u8"
    }
}
#[cfg(not(feature = "a"))]
impl W7<A> {
    pub fn f() -> &'static str {
        "B1:alias"
    }
}
#[cfg(feature = "a")]
impl W7<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// one impl holds both, under the predicate
pub struct W8<T>(pub T);
#[cfg(feature = "a")]
impl W8<u8> {
    pub fn f() -> &'static str {
        "B2:u8"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}
#[cfg(not(feature = "a"))]
impl W8<A> {
    pub fn f() -> &'static str {
        "B2:alias"
    }
}

// the call is in a trait impl under the predicate
pub struct W9<T>(pub T);
#[cfg(feature = "a")]
impl W9<u8> {
    pub fn f() -> &'static str {
        "B3:u8"
    }
}
#[cfg(not(feature = "a"))]
impl W9<A> {
    pub fn f() -> &'static str {
        "B3:alias"
    }
}
#[cfg(feature = "a")]
impl Go for W9<u8> {
    fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// an unconditional impl, with nothing else that may be built
pub struct S10;
impl S10 {
    pub fn f() -> &'static str {
        "B4:plain"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// `<Self>::f()` under the predicate of the function
pub struct S11;
#[cfg(feature = "a")]
impl S11 {
    pub fn f() -> &'static str {
        "B5:plain"
    }
    pub fn go() -> &'static str {
        <Self>::f() //@ f=E
    }
}

// the same predicate on the function and on the call, each on its own function item
pub struct S12;
impl S12 {
    #[cfg(feature = "b")]
    pub fn f() -> &'static str {
        "B6:plain"
    }
    #[cfg(feature = "b")]
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// the same predicate on two impl blocks of a type that has no other spelling
pub struct S13;
#[cfg(feature = "b")]
impl S13 {
    pub fn f() -> &'static str {
        "B7:plain"
    }
}
#[cfg(feature = "b")]
impl S13 {
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// the impls are in one conditional module
#[cfg(feature = "a")]
pub mod m14 {
    pub struct S14;
    impl S14 {
        pub fn f() -> &'static str {
            "B8:plain"
        }
        pub fn go() -> &'static str {
            Self::f() //@ f=E
        }
    }
}

// a cfg_attr on the impl header that gives the cfg, on the impl of both
pub struct S15;
#[cfg_attr(all(), cfg(feature = "a"))]
impl S15 {
    pub fn f() -> &'static str {
        "B9:plain"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=E
    }
}

// ---- group C: a plain type, where the other `f` is the default of a trait

// the function is in an impl under a cfg, the call in an impl under none
pub struct S20;
impl Tr for S20 {}
#[cfg(feature = "a")]
impl S20 {
    pub fn f() -> &'static str {
        "C1:inherent"
    }
}
impl S20 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the call is in a trait impl
pub struct S21;
impl Tr for S21 {}
#[cfg(feature = "a")]
impl S21 {
    pub fn f() -> &'static str {
        "C2:inherent"
    }
}
impl Go for S21 {
    fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// `<Self>::f()`
pub struct S22;
impl Tr for S22 {}
#[cfg(feature = "a")]
impl S22 {
    pub fn f() -> &'static str {
        "C3:inherent"
    }
}
impl S22 {
    pub fn go() -> &'static str {
        <Self>::f() //@ f=U:cfg
    }
}

// the call is under another feature
pub struct S23;
impl Tr for S23 {}
#[cfg(feature = "a")]
impl S23 {
    pub fn f() -> &'static str {
        "C4:inherent"
    }
}
#[cfg(feature = "b")]
impl S23 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the call is under the negation of the feature of the function
pub struct S24;
impl Tr for S24 {}
#[cfg(feature = "a")]
impl S24 {
    pub fn f() -> &'static str {
        "C5:inherent"
    }
}
#[cfg(not(feature = "a"))]
impl S24 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// two conditional impls give `f`, the call is in an impl under no cfg
pub struct S25;
#[cfg(feature = "a")]
impl S25 {
    pub fn f() -> &'static str {
        "C6:a"
    }
}
#[cfg(not(feature = "a"))]
impl S25 {
    pub fn f() -> &'static str {
        "C6:not-a"
    }
}
impl S25 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:several
    }
}

// two conditional impls give `f`, the call is in one of them (an edge to its own `f` is right, and lost: recall only)
pub struct S26;
#[cfg(feature = "a")]
impl S26 {
    pub fn f() -> &'static str {
        "C7:a"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=U:several
    }
}
#[cfg(not(feature = "a"))]
impl S26 {
    pub fn f() -> &'static str {
        "C7:not-a"
    }
}

// the impl of the function is in a conditional module
pub struct S27;
impl Tr for S27 {}
#[cfg(feature = "a")]
mod m27 {
    impl super::S27 {
        pub fn f() -> &'static str {
            "C8:inherent"
        }
    }
}
impl S27 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the cfg is on the function item
pub struct S28;
impl Tr for S28 {}
impl S28 {
    #[cfg(feature = "a")]
    pub fn f() -> &'static str {
        "C9:inherent"
    }
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// the cfg is a cfg_attr on the impl header of the function
pub struct S29;
impl Tr for S29 {}
#[cfg_attr(all(), cfg(feature = "a"))]
impl S29 {
    pub fn f() -> &'static str {
        "C10:inherent"
    }
}
impl S29 {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// ---- group D: the other `f` is a trait's, which no impl written for the type shows, or one for the type written another way

// a provided method of a standard trait the type implements: `Iterator::count`
pub struct D1(pub u8);
impl Iterator for D1 {
    type Item = u8;
    fn next(&mut self) -> Option<u8> {
        None
    }
}
#[cfg(feature = "a")]
impl D1 {
    pub fn count(self) -> usize {
        111
    }
}
pub fn d1() -> usize {
    D1::count(D1(0)) //@ count=U:cfg
}
impl D1 {
    pub fn go(self) -> usize {
        Self::count(self) //@ count=U:cfg
    }
}

// the reflexive `impl<T> From<T> for T`
pub struct D2(pub u8);
#[cfg(feature = "a")]
impl D2 {
    pub fn from(x: D2) -> D2 {
        D2(x.0 + 100)
    }
}
pub fn d2() -> u8 {
    D2::from(D2(1)).0 //@ from=U:cfg
}

// an impl of a repo trait for every type
pub trait TrB {
    fn g() -> u8;
}
impl<T> TrB for T {
    fn g() -> u8 {
        1
    }
}
pub struct D3;
#[cfg(feature = "a")]
impl D3 {
    pub fn g() -> u8 {
        2
    }
}
pub fn d3() -> u8 {
    D3::g() //@ g=U:cfg
}

// a provided method of `PartialEq`
pub struct D4(pub u8);
impl PartialEq for D4 {
    fn eq(&self, o: &D4) -> bool {
        self.0 == o.0
    }
}
#[cfg(feature = "a")]
impl D4 {
    pub fn ne(&self, _o: &D4) -> bool {
        true
    }
}
pub fn d4() -> bool {
    D4::ne(&D4(1), &D4(1)) //@ ne=U:cfg
}

// the reflexive `impl<T> TryFrom<U> for T where U: Into<T>`
pub struct D6(pub u8);
#[cfg(feature = "a")]
impl D6 {
    pub fn try_from(x: D6) -> Result<D6, core::convert::Infallible> {
        Ok(D6(x.0 + 100))
    }
}
pub fn d6() -> u8 {
    D6::try_from(D6(1)).unwrap().0 //@ try_from=U:cfg
}

// a trait impl for the type written with parentheses, `W<(u8)>`, which is `W<u8>`
pub trait TrF {
    fn f() -> &'static str;
}
pub struct W20<T>(pub T);
impl TrF for W20<(u8)> {
    fn f() -> &'static str {
        "D7:trait"
    }
}
#[cfg(feature = "a")]
impl W20<u8> {
    pub fn f() -> &'static str {
        "D7:inherent"
    }
}
impl W20<u8> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}
pub fn d7() -> &'static str {
    W20::<u8>::f() //@ f=U:cfg
}

// a trait impl for an array length written with a suffix, `[u8; 4usize]`
pub struct X21<T>(pub T);
impl TrF for X21<[u8; 4usize]> {
    fn f() -> &'static str {
        "D8:trait"
    }
}
#[cfg(feature = "a")]
impl X21<[u8; 4]> {
    pub fn f() -> &'static str {
        "D8:inherent"
    }
}
impl X21<[u8; 4]> {
    pub fn go() -> &'static str {
        Self::f() //@ f=U:cfg
    }
}

// ---- group E: nothing is conditional, and the inherent function wins over the trait impl for the same type written another way

pub struct W22<T>(pub T);
impl W22<u8> {
    pub fn f() -> &'static str {
        "E1:inherent"
    }
}
impl TrF for W22<(u8)> {
    fn f() -> &'static str {
        "E1:trait"
    }
}
pub fn e1() -> &'static str {
    W22::<(u8)>::f() //@ f=E
}

pub struct X23<T>(pub T);
impl X23<[u8; 4]> {
    pub fn f() -> &'static str {
        "E2:inherent"
    }
}
impl TrF for X23<[u8; 4usize]> {
    fn f() -> &'static str {
        "E2:trait"
    }
}
pub fn e2() -> &'static str {
    X23::<[u8; 4usize]>::f() //@ f=E
}

pub struct X24<T>(pub T);
impl X24<[u8; 4]> {
    pub fn f() -> &'static str {
        "E3:inherent"
    }
}
impl TrF for X24<[u8; 0x4]> {
    fn f() -> &'static str {
        "E3:trait"
    }
}
pub fn e3() -> &'static str {
    X24::<[u8; 04]>::f() //@ f=E
}

pub struct Y25<T>(pub T);
impl Y25<&u8> {
    pub fn f() -> &'static str {
        "E4:inherent"
    }
}
impl TrF for Y25<&  u8> {
    fn f() -> &'static str {
        "E4:trait"
    }
}
pub fn e4() -> &'static str {
    Y25::<& u8>::f() //@ f=E
}

// an associated constant is not a call: the path `Self::K` is no call site, whichever impl holds it
pub struct S30;
impl TrK for S30 {}
#[cfg(feature = "a")]
impl S30 {
    pub const K: &'static str = "C11:inherent";
}
impl S30 {
    pub fn go() -> &'static str {
        Self::K
    }
}

#[test]
fn group_a_the_feature_picks_the_function() {
    let a = cfg!(feature = "a");
    assert_eq!(W1::<u8>::go(), if a { "A1:alias" } else { "A1:u8" });
    assert_eq!(W2::<u8>::go(), if a { "A2:alias" } else { "A2:u8" });
    assert_eq!(<W3<u8> as Go>::go(), if a { "A3:alias" } else { "A3:u8" });
    assert_eq!(W4::<u8>::go(), if a { "A4:u8" } else { "A4:alias" });
    assert_eq!(W5::<u8>::go(), if a { "A5:alias" } else { "A5:u8" });
    assert_eq!(W6::<u8>::go(), if a { "A6:alias" } else { "A6:u8" });
    assert_eq!(W17::<u8>::go(), if a { "A7:generic" } else { "A7:u8" });
    assert_eq!(W19::<u8>::go(), if a { "A9:u8" } else { "A9:paren" });
}

#[test]
fn group_b_the_function_is_built_with_the_call() {
    #[cfg(feature = "a")]
    {
        assert_eq!(W7::<u8>::go(), "B1:u8");
        assert_eq!(W8::<u8>::go(), "B2:u8");
        assert_eq!(<W9<u8> as Go>::go(), "B3:u8");
        assert_eq!(S11::go(), "B5:plain");
        assert_eq!(m14::S14::go(), "B8:plain");
        assert_eq!(S15::go(), "B9:plain");
    }
    assert_eq!(S10::go(), "B4:plain");
    #[cfg(feature = "b")]
    {
        assert_eq!(S12::go(), "B6:plain");
        assert_eq!(S13::go(), "B7:plain");
    }
}

#[test]
fn group_c_a_trait_default_is_the_other_function() {
    let a = cfg!(feature = "a");
    assert_eq!(S20::go(), if a { "C1:inherent" } else { "Tr" });
    assert_eq!(<S21 as Go>::go(), if a { "C2:inherent" } else { "Tr" });
    assert_eq!(S22::go(), if a { "C3:inherent" } else { "Tr" });
    #[cfg(feature = "b")]
    assert_eq!(S23::go(), if a { "C4:inherent" } else { "Tr" });
    #[cfg(not(feature = "a"))]
    {
        // through a function pointer: the oracle reads the build with `a` on, where a call written here is not in the source it reads
        let go: fn() -> &'static str = S24::go;
        assert_eq!(go(), "Tr");
    }
    assert_eq!(S25::go(), if a { "C6:a" } else { "C6:not-a" });
    #[cfg(feature = "a")]
    assert_eq!(S26::go(), "C7:a");
    assert_eq!(S27::go(), if a { "C8:inherent" } else { "Tr" });
    assert_eq!(S28::go(), if a { "C9:inherent" } else { "Tr" });
    assert_eq!(S29::go(), if a { "C10:inherent" } else { "Tr" });
    assert_eq!(S30::go(), if a { "C11:inherent" } else { "TrK" });
}

#[test]
fn group_d_another_trait_gives_the_name() {
    let a = cfg!(feature = "a");
    assert_eq!(d1(), if a { 111 } else { 0 });
    assert_eq!(D1(0).go(), if a { 111 } else { 0 });
    assert_eq!(d2(), if a { 101 } else { 1 });
    assert_eq!(d3(), if a { 2 } else { 1 });
    assert_eq!(d4(), a);
    assert_eq!(d6(), if a { 101 } else { 1 });
    assert_eq!(W20::<u8>::go(), if a { "D7:inherent" } else { "D7:trait" });
    assert_eq!(d7(), if a { "D7:inherent" } else { "D7:trait" });
    assert_eq!(X21::<[u8; 4]>::go(), if a { "D8:inherent" } else { "D8:trait" });
}

#[test]
fn group_e_the_inherent_function_wins_whatever_the_trait_impl_is_spelled() {
    assert_eq!(e1(), "E1:inherent");
    assert_eq!(e2(), "E2:inherent");
    assert_eq!(e3(), "E3:inherent");
    assert_eq!(e4(), "E4:inherent");
}
