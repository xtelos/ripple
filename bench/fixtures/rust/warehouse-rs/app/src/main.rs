use warehouse_app::{cart::Cart as ShoppingCart, run};

fn main() {
    let units = run();
    let cart = ShoppingCart::new();
    println!("{} {}", units, ShoppingCart::total() + cart.count());
}
