#![allow(warnings)]
use std::borrow::Borrow;
pub struct Name(pub String);
impl Borrow<str> for Name {
    fn borrow(&self) -> &str { &self.0 }
}
impl AsRef<str> for Name {
    fn as_ref(&self) -> &str { &self.0 }
}
pub fn run(n: &Name) -> usize {
    let a: &Name = Name::borrow(n);
    let b: &str = <Name as Borrow<str>>::borrow(n);
    let c: &str = Borrow::<str>::borrow(n);
    let d: &str = Name::as_ref(n);
    let e: &str = AsRef::<str>::as_ref(n);
    let f: &Name = <Name as Borrow<Name>>::borrow(n);
    a.0.len() + b.len() + c.len() + d.len() + e.len() + f.0.len()
}
