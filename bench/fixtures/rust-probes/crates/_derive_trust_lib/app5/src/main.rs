use facade::Mark;

#[derive(Mark)]
struct S;

fn main() {
    println!("{}", S::who());
}
