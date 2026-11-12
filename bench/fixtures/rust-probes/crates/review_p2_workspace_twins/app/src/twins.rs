pub fn g() -> u32 {
    30
}

#[cfg(unix)]
pub fn plat() -> u32 {
    1
}
#[cfg(windows)]
pub fn plat() -> u32 {
    2
}

pub struct S;
impl S {
    #[cfg(unix)]
    pub fn p() -> u32 {
        1
    }
    #[cfg(windows)]
    pub fn p() -> u32 {
        2
    }
}

pub fn calls() -> u32 {
    plat() + S::p() + crate::imp::sys()
}
