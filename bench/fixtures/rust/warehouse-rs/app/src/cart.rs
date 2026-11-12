use money::Priced;
use std::fmt;

#[derive(Default)]
pub struct Cart {
    lines: Vec<u32>,
}

impl Cart {
    pub fn new() -> Self {
        Self::empty()
    }

    fn empty() -> Self {
        Cart { lines: Vec::new() }
    }

    pub fn add(&mut self, units: u32) {
        self.lines.push(units);
    }

    pub fn count(&self) -> u32 {
        self.lines.len() as u32
    }

    pub fn total() -> u32 {
        Cart::default().count() + crate::helper(1)
    }
}

impl Priced for Cart {
    fn price() -> u32 {
        Self::total()
    }

    fn describe(&self) -> String {
        format!("{}", Self::label())
    }
}

impl fmt::Display for Cart {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}", Self::price())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn totals() {
        assert_eq!(Cart::total(), Cart::new().count() + 1);
    }
}
