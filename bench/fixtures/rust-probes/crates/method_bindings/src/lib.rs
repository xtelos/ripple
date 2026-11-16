#![allow(dead_code, unused, non_upper_case_globals)]
// Which binding a receiver name is: a name bound twice, a `let` out of scope, an item or a closure that takes the name.

pub struct A;
pub struct B;

impl A {
    pub fn m(&self) -> u8 {
        1
    }
}

impl B {
    pub fn m(&self) -> u8 {
        2
    }
}

pub fn y() {}

pub trait Callable {
    fn m(&self) -> u8;
}

impl<F: Fn()> Callable for F {
    fn m(&self) -> u8 {
        9
    }
}

pub fn shadow_let(x: &A) -> u8 {
    let x: &B = &B;
    x.m() //~ m=U:more than once
}

pub fn shadow_closure(x: &A) -> u8 {
    let f = |x: &B| x.m(); //~ m=U:more than once
    f(&B)
}

pub fn closure_leaves_the_param(x: &A) -> u8 {
    let f = |x: &B| 0;
    x.m() //~ m=U:more than once
}

pub fn shadow_match(x: &A, v: Option<&B>) -> u8 {
    match v {
        Some(x) => x.m(), //~ m=U:more than once
        None => 0,
    }
}

pub fn shadow_for(x: &A, ys: &[B]) -> u8 {
    let mut t = 0;
    for x in ys {
        t += x.m(); //~ m=U:more than once
    }
    t
}

pub fn shadow_if_let(x: &A, v: Option<&B>) -> u8 {
    if let Some(x) = v {
        return x.m(); //~ m=U:more than once
    }
    0
}

pub fn inner_block_then_outer(x: &A) -> u8 {
    {
        let y: &B = &B;
        y.m(); //~ m=E
    }
    x.m() //~ m=E
}

// `y` after the block is the function `y` (a function item is a `Fn`, which `Callable` is for), not the block's `let`
pub fn let_out_of_scope() -> u8 {
    {
        let y: B = B;
        y.m(); //~ m=U:later probe step
    }
    y.m() //~ m=U:not in effect
}

pub fn let_used_before() -> u8 {
    let a = y.m(); //~ m=U:not in effect
    let y: &B = &B;
    a + y.m() //~ m=E
}

// an item of a block that takes the parameter's name is what the name is in that block
pub fn block_const_shadows(x: &A) -> u8 {
    const x: B = B;
    x.m() //~ m=U:item of the same name
}

pub fn block_static_shadows(a: &A) -> u8 {
    {
        static a: B = B;
        a.m() //~ m=U:item of the same name
    }
}

pub fn item_after_use_in_block(x: &A) -> u8 {
    let r = x.m(); //~ m=U:item of the same name
    const x: B = B;
    r
}

pub fn typed_param_ok(x: &A) -> u8 {
    x.m() //~ m=E
}

pub fn param_with_pattern((x, z): (&A, &B)) -> u8 {
    x.m() //~ m=U:does not write
}

pub fn struct_pattern_param(Wrapper { a }: Wrapper) -> u8 {
    a.m() //~ m=U:does not write
}

pub struct Wrapper<'a> {
    pub a: &'a A,
}

pub struct n;

impl n {
    pub fn m(&self) -> u8 {
        3
    }
}

// `n` here is the unit struct `n` (a pattern that binds nothing), not a new binding
pub fn unit_struct_binding() -> u8 {
    let n: &n = &n;
    n.m() //~ m=U:pattern
}

pub fn unit_struct_pattern_param(n: &n) -> u8 {
    n.m() //~ m=U:pattern
}

pub fn unit_struct_in_block() -> u8 {
    struct k;
    impl k {
        pub fn m(&self) -> u8 {
            3
        }
    }
    let k: &k = &k;
    k.m() //~ m=U:item of the same name
}

// the arguments of a standard macro are read again as code: where a `let` is in effect is still told by where the call is in the file
pub fn in_std_macro_arguments(x: &A) -> u8 {
    println!("{}", y.m()); //~ m=U:not in effect
    let y: &B = &B;
    println!("{}", y.m()); //~ m=E
    println!("{}", (|x: &B| x.m())(&B)); //~ m=U:more than once
    println!("{}", x.m()); //~ m=E
    0
}
