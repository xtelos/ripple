#[macro_use]
mod macros;

macro_rules! mkmod_here {
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

mkmod_here!(here);

mod genmod;
mod nomacro;
