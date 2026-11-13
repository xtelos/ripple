// clap_derive shape `impl clap::Tr for S`: with a local `mod clap`, the derive implements the REPO trait. Expect: prints "local clap module".
mod clap {
    pub trait Tr {
        fn who() -> &'static str {
            "local clap module"
        }
    }
}
#[derive(pm::RelClap)]
struct S;
fn main() {
    println!("{}", <S as clap::Tr>::who());
}
