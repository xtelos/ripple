#![allow(warnings)]
// A struct declared in a function body that takes the name of a module's struct: rust-analyzer names both `S::go` alike, so the
// answers for the two calls are written in bench/rs_probe_oracle.py (OVERRIDES) and proven by the test below.
pub struct S;

impl S {
    pub fn go(&self) -> u8 {
        1
    }
}

pub fn module_one(s: &S) -> u8 {
    s.go() //~ go=E
}

pub fn local_one() -> u8 {
    struct S;
    impl S {
        fn go(&self) -> u8 {
            2
        }
    }
    let s: &S = &S;
    s.go() //~ go=E
}

pub fn local_param(t: u8) -> u8 {
    struct S;
    impl S {
        fn go(&self) -> u8 {
            4
        }
    }
    fn inner(s: &S) -> u8 {
        s.go() //~ go=E
    }
    inner(&S) + t
}

#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::module_one(&super::S), 1);
        assert_eq!(super::local_one(), 2);
        assert_eq!(super::local_param(0), 4);
    }
}
