pub trait Conv<X> {
    fn conv() -> u32;
}
pub struct S;
impl Conv<u8> for S {
    fn conv() -> u32 {
        8
    }
}
impl<T> Conv<T> for T {
    fn conv() -> u32 {
        1
    }
}
pub fn run() -> u32 {
    <S as Conv<S>>::conv()
}
#[test]
fn which() {
    assert_eq!(run(), 1);
}
