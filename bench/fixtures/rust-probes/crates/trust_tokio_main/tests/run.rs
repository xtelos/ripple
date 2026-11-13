#[test]
fn the_binary_ran_the_function_written_in_its_body() {
    let status = std::process::Command::new(env!("CARGO_BIN_EXE_trust_tokio_main")).status().unwrap();
    assert!(status.success());
}
