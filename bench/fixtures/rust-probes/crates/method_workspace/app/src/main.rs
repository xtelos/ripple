fn main() {
    let c: kit::Cfg = kit::Cfg { n: 1 };
    let r: &kit::Cfg = &c;
    println!("{}", r.get()); //~ get=E
}
