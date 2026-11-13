mod other { pub fn helper() -> u32 { 1 } }
pub fn t_path() -> u32 { std::println!("{}", other::helper()) }
pub fn t_global() -> u32 { ::std::println!("{}", other::helper()) }
#[test]
fn hijacked() {
    assert!(t_path() == 2 && t_global() == 2);
}
