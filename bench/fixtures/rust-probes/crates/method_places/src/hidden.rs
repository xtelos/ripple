pub trait Secret {
    fn secret(&self) -> u32;
}

impl Secret for crate::Circle {
    fn secret(&self) -> u32 {
        2
    }
}

// `Circle::secret` is private to `shapes`: here the trait's is the one found
pub fn call_secret(c: &crate::Circle) -> u32 {
    c.secret() //~ secret=U:not visible
}

pub fn call_radius(c: &crate::Circle) -> u32 {
    c.radius() //~ radius=E
}
