#![allow(warnings)]
pub struct Counter(pub u32);
impl Iterator for Counter {
    type Item = u32;
    fn next(&mut self) -> Option<u32> { None }
}
pub fn run() -> usize {
    let mut c = Counter(0);
    let a = Counter::next(&mut c);
    let b = Iterator::next(&mut c);
    let d = <Counter as Iterator>::next(&mut c);
    let e = Iterator::map(Counter(1), |x| x + 1).count();
    let f = Counter::count(Counter(2));
    let g = <Counter as IntoIterator>::into_iter(Counter(3));
    let h = IntoIterator::into_iter(Counter(4));
    let i = Iterator::size_hint(&c);
    let _ = (a, b, d, g, h, i);
    e + f
}
