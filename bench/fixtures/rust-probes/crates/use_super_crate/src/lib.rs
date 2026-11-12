#![allow(warnings)]
pub fn top() -> u32 { 0 }
pub mod m {
    pub fn f() -> u32 { 1 }
    pub mod n {
        use super::f;
        pub fn g() -> u32 { f() + super::f() + super::super::top() + crate::top() + self::h() }
        pub fn h() -> u32 { 2 }
        pub mod o {
            pub fn k() -> u32 { super::h() + super::super::f() + super::super::super::top() }
        }
    }
}
