fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    generate();
}

fn generate() {}
