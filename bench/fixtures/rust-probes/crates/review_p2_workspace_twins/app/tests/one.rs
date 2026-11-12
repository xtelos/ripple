mod common;

fn local_helper() -> u32 {
    11
}

#[test]
fn one() {
    assert_eq!(common::setup(), 11);
}
