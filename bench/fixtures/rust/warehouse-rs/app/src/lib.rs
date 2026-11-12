//! The warehouse app: the library of a package that also builds a bin of the same name.
pub mod cart;
pub mod shipping;

#[path = "old/legacy_impl.rs"]
mod legacy;

mod support {
    pub fn fixture() -> u32 {
        3
    }
}

pub use cart::Cart;

use tax_rules::vat as vat_of;
use money::{
    discount::{self, seasonal::holiday},
    Priced,
};
use shipping::*;

pub struct Crate;

pub struct Wrap(pub u32);

impl Priced for Crate {
    fn price() -> u32 {
        7
    }

    fn describe(&self) -> String {
        String::from("crate")
    }
}

#[cfg(unix)]
fn platform() -> &'static str {
    "unix"
}

#[cfg(not(unix))]
fn platform() -> &'static str {
    "other"
}

fn helper(x: u32) -> u32 {
    x + 1
}

macro_rules! doubled {
    ($e:expr) => {
        helper($e) * 2
    };
}

pub fn run() -> u32 {
    let a = vat_of(2);
    let b = money::quote(3);
    let c = holiday(10);
    let d = discount::clamp(5);
    let e = legacy::old_price();
    let mut f = Cart::new();
    let g = <Crate as Priced>::price();
    let h = Crate::price();
    let i = <Crate as Priced>::label();
    let j = doubled!(helper(4));
    println!("{}", helper(a));
    let items = vec![helper(1), money::summer(2)];
    assert_eq!(helper(2), 3);
    let k = platform();
    let l = support::fixture();
    let m = dispatch(l);
    let n = Priced::describe(&Crate);
    let o = Cart::default();
    let p = money::pick_max() + money::pick_min();
    let q = std::cmp::max(1, 2);
    let r = u32::from_str_radix("7", 10);
    let w = Wrap(5);
    let s = Status::closed();
    let t = Status::open();
    let u = generic_price::<Crate>();
    f.add(1);
    a + b + c + d + e + g + h + j + m + p + q + items.len() as u32 + o.count() + k.len() as u32 + i.len() as u32 + n.len() as u32 + r.is_ok() as u32 + w.0 + u + matches!(s, Status::Open) as u32 + matches!(t, Status::Open) as u32
}

pub fn generic_price<T: Priced>() -> u32 {
    T::price()
}

pub fn closures() -> u32 {
    let helper = |x: u32| x * 3;
    fn inner() -> u32 {
        helper_free()
    }
    fn helper_free() -> u32 {
        4
    }
    helper(1) + inner()
}
