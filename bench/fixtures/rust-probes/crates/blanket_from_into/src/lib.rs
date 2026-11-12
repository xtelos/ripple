#![allow(warnings)]
pub struct Celsius(pub f64);
pub struct Kelvin(pub f64);
impl From<f64> for Celsius {
    fn from(v: f64) -> Self { Celsius(v) }
}
impl From<Celsius> for Kelvin {
    fn from(c: Celsius) -> Self { Kelvin(c.0 + 273.0) }
}
pub fn run() -> Kelvin {
    let a = Celsius::from(1.5);
    let b = <Celsius as From<f64>>::from(2.5);
    let c2 = Celsius::from(Celsius(3.0));
    let d = <Celsius as From<Celsius>>::from(Celsius(1.0));
    let e: Kelvin = Into::into(c2);
    let f: Celsius = From::from(4.0);
    let g = <f64 as Into<Celsius>>::into(5.0);
    let h = f64::from(1u8);
    let i = String::from("x");
    let j: Celsius = 1.0.into();
    let _ = (b, d, e, f, g, h, i, j);
    Kelvin::from(a)
}
