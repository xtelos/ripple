#![allow(dead_code, unused)]
// A `#[macro_use]` module whose file ripple does not follow (a `cfg_attr` path, and no file of the module's own name) holds a textual
// `println!` that rebinds `s`: rustc calls Other::m, where a ripple that did not know the file is unread would draw S::m.
#[macro_use]
#[cfg_attr(all(), path = "real_macros.rs")]
mod macros;

pub struct S;
pub struct Other;

impl S {
    pub fn m(&self) -> &'static str {
        "S::m"
    }
}

impl Other {
    pub fn m(&self) -> &'static str {
        "Other::m"
    }
}

pub fn f(s: &S) -> &'static str {
    println!(s);
    s.m() //~ m=U:did not read
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_call_runs_the_method_of_the_other_type() {
        assert_eq!(super::f(&super::S), "Other::m");
    }
}
