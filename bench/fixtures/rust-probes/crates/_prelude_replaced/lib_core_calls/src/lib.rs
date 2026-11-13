pub fn t_core() -> u32 { core::mark() }
#[test]
fn hijacked() {
    assert!(t_core() == 2);
}
