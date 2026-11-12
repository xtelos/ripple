#![allow(warnings)]
mod x {
    pub fn g() -> u32 { 1 }
    pub fn k() -> u32 { 5 }
}
fn g() -> u32 { 100 }
pub fn run() -> String {
    let a = format!("{}", { use crate::x::g; g() });
    let b = vec![{ fn g() -> u32 { 40 } g() }];
    let c = vec![(|| g())()];
    let d = format!("{}", { let g = || 7; g() });
    let e = format!("{}", { use crate::x::g as kk; kk() + { use crate::x::k; k() } });
    assert!({ use crate::x::g; g() } == 1);
    assert_eq!({ fn g() -> u32 { 1 } g() }, 1);
    println!("{}", { fn g() -> u32 { 0 } g() });
    let f = dbg!({ use crate::x::k as g; g() });
    let h = format!("{}", format!("{}", { use crate::x::g; g() }));
    let i = vec![(|g: fn() -> u32| g())(x::k)];
    assert_eq!(b, vec![40]); assert_eq!(c, vec![100]);
    assert_eq!(format!("{}", { fn g() -> u32 { 0 } g() }), "0");
    let _ = (b, c, f, i);
    format!("{}{}{}{}{}", a, d, e, h, g())
}
pub fn matches_args(o: Option<u32>) -> bool {
    matches!(g(), 100) && matches!(o, Some(n) if n > g())
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::run(), "1761100");
        assert!(super::matches_args(Some(200)));
    }
}
