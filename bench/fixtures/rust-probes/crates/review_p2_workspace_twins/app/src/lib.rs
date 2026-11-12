pub mod imp;
pub mod twins;

pub fn f() -> u32 {
    kore::shared() + kore::re::renamed()
}

pub fn g() -> u32 {
    3
}

pub mod a {
    pub mod b {
        pub fn up() -> u32 {
            super::super::g()
        }
    }
}

pub fn in_block() -> u32 {
    use crate::a::b::up;
    up()
}

pub fn std_macro_block() -> String {
    // the block inside the macro imports twins::g, which shadows crate::g
    format!("{}", { use crate::twins::g; g() })
}

pub fn std_macro_nested_fn() -> Vec<u32> {
    // the block inside vec! declares its own g, which shadows crate::g
    vec![{ fn g() -> u32 { 40 } g() }]
}

#[cfg(test)]
mod proof {
    #[test]
    fn which_runs() {
        assert_eq!(super::std_macro_block(), "30");
        assert_eq!(super::std_macro_nested_fn(), vec![40]);
        assert_eq!(super::a::b::up(), 3);
    }
}
