macro_rules! println {
    ($i:ident) => {
        let $i = &crate::Other;
    };
}
