#![allow(dead_code, unused, unexpected_cfgs)]
// An extension trait of another crate gives `g` to every type, and a conditional inherent `g` is what the call finds only where it is built.
// The tests assert which `g` ran under `cargo test --no-default-features` (a off: the foreign trait's) and `cargo test` (a on: the inherent one).
// Markers as in path_self_cfg: the at-sign marker `g=U:text` is unresolved with a reason that contains the text.
use extblanket::Ext;

pub struct S;
#[cfg(feature = "a")]
impl S {
    pub fn g() -> &'static str {
        "inherent"
    }
}
impl S {
    pub fn go() -> &'static str {
        Self::g() //@ g=U:cfg
    }
}
pub fn run() -> &'static str {
    S::g() //@ g=U:cfg
}

#[test]
fn the_feature_picks_the_function() {
    let want = if cfg!(feature = "a") { "inherent" } else { "ext-blanket" };
    assert_eq!(S::go(), want);
    assert_eq!(run(), want);
}
