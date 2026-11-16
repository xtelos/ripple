pub struct Circle {
    pub r: u32,
}

impl Circle {
    pub fn radius(&self) -> u32 {
        self.r
    }
    fn secret(&self) -> u32 {
        1
    }
    pub fn helper(&self) -> u32 {
        self.secret() //~ secret=E
    }
}
