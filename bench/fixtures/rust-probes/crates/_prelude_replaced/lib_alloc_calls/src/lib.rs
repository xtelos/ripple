pub fn t_alloc() -> u32 { alloc::mark() }
#[test]
fn hijacked() {
    assert!(t_alloc() == 2);
}
