//! Prices and taxes: the crate the app depends on by path.
mod rates;
pub mod discount;

pub use discount::seasonal::*;
pub use rates::{base_rate, Rate};

/// A trait with a static function, one with a default body, and a method.
pub trait Priced {
    fn price() -> u32;

    fn label() -> String {
        String::from("item")
    }

    fn describe(&self) -> String;

    fn twice() -> String {
        format!("{}{}", Self::label(), Self::label())
    }
}

pub fn quote(units: u32) -> u32 {
    let rate = base_rate();
    apply_tax(units * rate.cents)
}

fn apply_tax(amount: u32) -> u32 {
    amount + amount / 10
}

pub fn max(a: u32, b: u32) -> u32 {
    if a > b {
        a
    } else {
        b
    }
}

pub fn pick_max() -> u32 {
    // a glob from outside the repo in a block could bring a `max` that shadows this module's own
    use std::cmp::*;
    max(1, 2)
}

pub fn pick_min() -> u32 {
    // no `min` is declared here, so the glob can only be the one that provides it
    use std::cmp::*;
    min(1, 2)
}
