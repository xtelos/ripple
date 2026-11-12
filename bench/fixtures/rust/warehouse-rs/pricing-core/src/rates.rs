pub struct Rate {
    pub cents: u32,
}

pub fn base_rate() -> Rate {
    Rate::flat(5)
}

impl Rate {
    pub fn flat(cents: u32) -> Self {
        Self::new(cents)
    }

    fn new(cents: u32) -> Self {
        Rate { cents }
    }
}
