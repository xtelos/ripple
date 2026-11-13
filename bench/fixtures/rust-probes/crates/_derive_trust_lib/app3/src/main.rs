use thing2::Mark;

#[derive(Mark)]
struct S;

fn main() {
    println!("{}", S::who());
}
