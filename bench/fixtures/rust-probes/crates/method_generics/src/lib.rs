#![allow(dead_code, unused)]
// Generic types: an impl for every type argument, impls for one argument each, an impl with bounds, and a type that may be unsized.

use std::fmt::Display;

pub struct W<T> {
    pub v: T,
}

impl<T> W<T> {
    pub fn get(&self) -> &T {
        &self.v
    }
    pub fn plain(&self) -> u8 {
        0
    }
}

impl W<u8> {
    pub fn only_u8(&self) -> u8 {
        self.v
    }
    pub fn both(&self) -> u8 {
        1
    }
}

impl W<u16> {
    pub fn both(&self) -> u8 {
        2
    }
}

// an inherent impl with a bound applies only to the types that meet it
impl<T: Display> W<T> {
    pub fn show(&self) -> String {
        self.v.to_string()
    }
}

pub trait Show {
    fn show(&self) -> String;
}

impl<T> Show for W<T> {
    fn show(&self) -> String {
        String::new()
    }
}

pub struct NotDisplay;

pub fn every_type(w: &W<u8>) -> u8 {
    w.get(); //~ get=E
    w.plain() //~ plain=E
}

pub fn the_u8_impl(w: &W<u8>) -> u8 {
    w.only_u8() //~ only_u8=E
}

pub fn the_u8_impl_of_two(w: &W<u8>) -> u8 {
    w.both() //~ both=E
}

pub fn the_u16_impl_of_two(w: &W<u16>) -> u8 {
    w.both() //~ both=E
}

pub fn generic_argument<T>(w: &W<T>) -> u8 {
    w.plain() //~ plain=E
}

pub fn inferred_argument() -> u8 {
    let w: W<_> = W { v: 1u8 };
    w.only_u8() //~ only_u8=U:type arguments
}

pub fn inferred_argument_ref() -> u8 {
    let w: &W<_> = &W { v: 1u8 };
    w.plain() //~ plain=E
}

pub fn bound_not_met(w: &W<NotDisplay>) -> String {
    w.show() //~ show=U:bounds
}

pub fn bound_met(w: &W<u8>) -> String {
    w.show() //~ show=U:bounds
}

pub fn through_self(w: &W<u8>) -> u8 {
    w.get(); //~ get=E
    0
}

impl W<u8> {
    pub fn inside(&self) -> u8 {
        self.both() //~ both=E
    }
}

impl<T> W<T> {
    pub fn inside_generic(&self) -> u8 {
        self.plain() //~ plain=E
    }
}

pub struct Slice<T: ?Sized> {
    pub v: Box<T>,
}

impl<T> Slice<T> {
    pub fn hint(&self) -> u8 {
        1
    }
}

pub trait Hint {
    fn hint(&self) -> u8;
}

impl<T: ?Sized> Hint for Slice<T> {
    fn hint(&self) -> u8 {
        2
    }
}

// the inherent impl is for sized arguments only: `str` is not one, so the trait's is the method found
pub fn unsized_argument(s: &Slice<str>) -> u8 {
    s.hint() //~ hint=U:unsized
}

pub fn sized_argument(s: &Slice<u8>) -> u8 {
    s.hint() //~ hint=U:unsized
}
