pub fn t_std() -> u32 { std::mark() }
pub fn t_std_global() -> u32 { ::std::mark() }
#[test]
fn hijacked() {
    assert!(t_std() == 2 && t_std_global() == 2);
}
