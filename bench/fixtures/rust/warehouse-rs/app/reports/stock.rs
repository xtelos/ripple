// a bin whose root is not under src/ (the [[bin]] path in Cargo.toml)
use money::quote;

fn main() {
    println!("{}", quote(warehouse_app::shipping::dispatch(5)));
}
