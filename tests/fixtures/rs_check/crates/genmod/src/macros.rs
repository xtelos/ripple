macro_rules! mkmod {
    ($n:ident) => {
        mod $n {
            #[test]
            fn g() {
                assert_eq!(1, 2);
            }

            #[test]
            fn ok() {}
        }
    };
}
