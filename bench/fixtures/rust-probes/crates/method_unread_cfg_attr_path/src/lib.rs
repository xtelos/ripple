#![allow(dead_code, unused)]
// The `cfg_attr` path sends the module to a file outside the crate's source directory, and a file named for the module sits beside
// the crate root unused (rustc never reads it, a reader that looks for the default file reads that one). The file that is built holds
// a textual `println!` that rebinds `s`: rustc calls Other::m.
#[macro_use]
#[cfg_attr(all(), path = "../shared/real_macros.rs")]
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
