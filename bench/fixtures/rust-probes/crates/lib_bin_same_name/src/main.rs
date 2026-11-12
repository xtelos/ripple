mod local;
fn f() -> u32 { 50 }
fn main() {
    println!("{}", app::f() + f() + local::g() + app::util::u() + crate::f());
}
#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::f(), 50);
        assert_eq!(crate::local::g(), 51);
    }
}
#[cfg(test)]
mod proof2 {
    #[test]
    fn line4() { assert_eq!(app::f(), 1); assert_eq!(super::f(), 50); assert_eq!(crate::f(), 50); }
}
