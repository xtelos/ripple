mod rules;

pub use rules::weight_limit;

pub enum Status {
    Open,
    Closed(u32),
}

impl Status {
    pub fn closed() -> Self {
        Self::Closed(0)
    }

    pub fn open() -> Status {
        Status::Open
    }
}

pub fn dispatch(weight: u32) -> u32 {
    if weight > weight_limit() {
        0
    } else {
        rules::surcharge(weight)
    }
}
