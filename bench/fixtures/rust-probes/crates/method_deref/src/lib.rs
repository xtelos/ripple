#![allow(dead_code, unused)]
// A type of the repo that derefs to another: a method the wrapper has is found at its own step, and one it lacks is reached through
// the Deref impl at a later step.

use std::ops::Deref;

pub struct Inner;

impl Inner {
    pub fn both(&self) -> u8 {
        1
    }
    pub fn only_inner(&self) -> u8 {
        2
    }
}

pub struct Outer {
    pub inner: Inner,
}

impl Deref for Outer {
    type Target = Inner;
    fn deref(&self) -> &Inner {
        &self.inner
    }
}

impl Outer {
    pub fn both(&self) -> u8 {
        3
    }
}

pub fn wrapper_has_it(o: &Outer) -> u8 {
    o.both() //~ both=E
}

pub fn reached_through_deref(o: &Outer) -> u8 {
    o.only_inner() //~ only_inner=U:no inherent
}

pub fn the_target_itself(i: &Inner) -> u8 {
    i.both() //~ both=E
}

pub fn deref_inside_the_impl(o: &Outer) -> u8 {
    o.deref().both() //~ deref=U:no inherent; both=U:not a plain name
}
