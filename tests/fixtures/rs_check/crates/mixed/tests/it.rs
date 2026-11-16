mod common;

#[test]
fn t_it() {
    assert_eq!(mixed::add(1, 2), 3);
}

#[test]
fn same() {
    assert_eq!(mixed::add(1, 1), 2);
}
