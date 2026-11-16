#![allow(dead_code, unused)]
// Inherent impls of one type in several modules and files, behind visibility, and inside function bodies.

mod extra;
pub mod hidden;
mod shapes;

pub use shapes::Circle;

pub fn use_circle(c: &Circle) -> u32 {
    c.radius() //~ radius=E
}

pub fn use_extra(c: &Circle) -> u32 {
    c.area() //~ area=E
}

pub fn use_inline(c: &Circle) -> u32 {
    c.inline_only() //~ inline_only=E
}

pub mod nested {
    impl crate::Circle {
        pub fn inline_only(&self) -> u32 {
            self.r
        }
    }
}

pub fn local_impl() -> u32 {
    struct Local;
    impl Local {
        fn go(&self) -> u32 {
            5
        }
    }
    let l: &Local = &Local;
    l.go() //~ go=E
}

pub fn local_impl_twin() -> u32 {
    struct Twin;
    impl Twin {
        fn go(&self) -> u32 {
            6
        }
    }
    let l: &Twin = &Twin;
    l.go() //~ go=E
}

pub fn local_impl_after_use() -> u32 {
    let l: &Later = &Later;
    return l.go(); //~ go=E
    struct Later;
    impl Later {
        fn go(&self) -> u32 {
            7
        }
    }
}
