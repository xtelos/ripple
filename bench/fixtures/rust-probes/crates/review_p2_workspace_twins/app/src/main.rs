fn main() {
    println!("{}", app::f() + app::g());
}
#[cfg(test)]
mod proof { #[test] fn t() { assert_eq!(app::g(), 3); } }
