impl crate::Cfg {
    pub fn more(&self) -> u8 {
        self.get() //~ get=E
    }
}
