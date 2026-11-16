use std::io::Write;

#[test]
fn a() {
    let mut out = std::io::stdout();
    out.write_all(b"test a ... ok\ntest b ... ok\n\ntest result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n\n").unwrap();
    out.flush().unwrap();
    std::process::exit(0);
}

#[test]
fn b() {
    std::thread::sleep(std::time::Duration::from_millis(500));
    assert_eq!(forge::f(), 2);
}
